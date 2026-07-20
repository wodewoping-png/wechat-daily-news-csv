from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


CSV_COLUMNS = [
    "title",
    "account_name",
    "source",
    "tags",
    "priority",
    "publish_time",
    "author",
    "digest",
    "url",
    "crawled_at",
    "content_preview",
    "clean_text",
    "ai_summary",
    "importance_score",
    "alert_level",
    "push_status",
]

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate weekly/monthly rollups from daily CSV files")
    parser.add_argument("--period", choices=["auto", "week", "month", "both"], default="auto")
    parser.add_argument("--week", default="last-week", help="last-week, this-week, or YYYY-MM-DD")
    parser.add_argument("--month", default="last-month", help="last-month, this-month, or YYYY-MM")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Generate a partial summary even when one or more daily CSV files are absent",
    )
    return parser.parse_args(argv)


def start_of_week(value: date) -> date:
    return value - timedelta(days=value.weekday())


def last_day_of_month(value: date) -> date:
    if value.month == 12:
        next_month = date(value.year + 1, 1, 1)
    else:
        next_month = date(value.year, value.month + 1, 1)
    return next_month - timedelta(days=1)


def resolve_week_range(value: str, today: date | None = None) -> tuple[date, date, str]:
    today = today or datetime.now(BEIJING_TZ).date()
    normalized = (value or "last-week").strip().lower()
    if normalized in {"last-week", "previous-week"}:
        start_date = start_of_week(today) - timedelta(days=7)
    elif normalized == "this-week":
        start_date = start_of_week(today)
    else:
        start_date = start_of_week(datetime.strptime(value, "%Y-%m-%d").date())
    end_date = start_date + timedelta(days=6)
    iso_year, iso_week, _ = start_date.isocalendar()
    return start_date, end_date, f"{iso_year}-W{iso_week:02d}"


def resolve_month_range(value: str, today: date | None = None) -> tuple[date, date, str]:
    today = today or datetime.now(BEIJING_TZ).date()
    normalized = (value or "last-month").strip().lower()
    if normalized in {"last-month", "previous-month"}:
        end_date = today.replace(day=1) - timedelta(days=1)
        start_date = end_date.replace(day=1)
    elif normalized == "this-month":
        start_date = today.replace(day=1)
        end_date = last_day_of_month(start_date)
    else:
        parsed = datetime.strptime(value, "%Y-%m").date()
        start_date = date(parsed.year, parsed.month, 1)
        end_date = last_day_of_month(start_date)
    return start_date, end_date, start_date.strftime("%Y-%m")


def iter_dates(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def load_articles(
    repo_root: Path,
    start_date: date,
    end_date: date,
    allow_missing: bool = False,
) -> tuple[list[dict[str, str]], list[str]]:
    articles: list[dict[str, str]] = []
    missing: list[str] = []
    seen: set[str] = set()

    for target_date in iter_dates(start_date, end_date):
        path = repo_root / "csv" / f"{target_date.isoformat()}.csv"
        if not path.exists():
            missing.append(target_date.isoformat())
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw_row in reader:
                row = {column: make_cell_text(raw_row.get(column) or "") for column in CSV_COLUMNS}
                row["source"] = row["source"] or row["account_name"]
                unique_key = row["url"] or "\x1f".join(
                    [row["title"], row["account_name"], row["publish_time"]]
                )
                if unique_key in seen:
                    continue
                seen.add(unique_key)
                articles.append(row)

    if missing and not allow_missing:
        raise FileNotFoundError(
            "Missing daily CSV files: " + ", ".join(missing) + ". "
            "Upload them first or rerun with --allow-missing for an explicitly partial report."
        )

    articles.sort(key=lambda article: article.get("publish_time", ""), reverse=True)
    return articles, missing


def write_rollup_csv(articles: list[dict[str, str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows({column: article.get(column, "") for column in CSV_COLUMNS} for article in articles)


def parse_tags(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，]", value or "") if part.strip()]


def make_cell_text(value: str) -> str:
    return " ".join((value or "").split())


def make_excerpt(value: str, max_chars: int) -> str:
    normalized = make_cell_text(value)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip() + "..."


def article_rank_key(article: dict[str, str]) -> tuple[float, str]:
    priority_weight = {"high": 3.0, "normal": 2.0, "low": 1.0}.get(
        article.get("priority", "normal"), 0.0
    )
    alert_weight = {"high": 3.0, "medium": 2.0, "low": 1.0}.get(
        article.get("alert_level", ""), 0.0
    )
    try:
        score = float(article.get("importance_score") or 0)
    except ValueError:
        score = 0.0
    return priority_weight + alert_weight + score, article.get("publish_time", "")


def source_name(article: dict[str, str]) -> str:
    return article.get("account_name") or article.get("source") or "未命名公众号"


def build_ai_prompt(
    period_name: str,
    slug: str,
    start_date: date,
    end_date: date,
    articles: list[dict[str, str]],
    account_counter: Counter[str],
    tag_counter: Counter[str],
) -> str:
    source_stats = "、".join(f"{name}({count})" for name, count in account_counter.most_common(20))
    tag_stats = "、".join(f"{name}({count})" for name, count in tag_counter.most_common(20)) or "无"
    selected = sorted(articles, key=article_rank_key, reverse=True)[:80]
    article_lines = []
    for idx, article in enumerate(selected, 1):
        summary = article.get("ai_summary") or article.get("digest") or article.get("content_preview")
        article_lines.append(
            f"{idx}. [{source_name(article)}] {article.get('title', '')}"
            f"｜{make_excerpt(summary, 240)}"
        )

    return f"""请基于下面提供的微信公众号文章元数据，撰写中文{period_name}趋势研判。

周期：{slug}（{start_date.isoformat()} 至 {end_date.isoformat()}）
文章总数：{len(articles)}
主要来源：{source_stats}
高频标签：{tag_stats}

代表性文章：
{chr(10).join(article_lines)}

要求：
1. 只依据给定材料，不补充未提供的事实或数字。
2. 输出 Markdown 正文，不要输出一级或二级标题。
3. 包含“核心趋势”“值得关注的信号”“下期观察点”三个三级标题。
4. 区分事实归纳与推断，对证据不足处明确说明。
5. 控制在 900 至 1400 个中文字符。
"""


def extract_response_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            return content.strip()

    # Retain support for Responses-style payloads when a compatible proxy returns one.
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    return "\n\n".join(parts).strip()


def generate_ai_analysis(prompt: str) -> tuple[str | None, str | None]:
    api_key = os.getenv("ZAI_API_KEY", "").strip()
    if not api_key:
        return None, None

    base_url = (
        os.getenv("ZAI_BASE_URL", "").strip().rstrip("/")
        or "https://api.z.ai/api/paas/v4"
    )
    model = os.getenv("ZAI_MODEL", "glm-5.1").strip() or "glm-5.1"
    request_payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一名谨慎的中文产业情报分析师。严格依据输入材料，"
                    "不虚构事实，并清楚标注推断和不确定性。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0.3,
        "max_tokens": 2200,
        "stream": False,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        if os.getenv("ZAI_REQUIRED", "").strip().lower() in {"1", "true", "yes"}:
            raise RuntimeError(f"Z.AI summary request failed: {exc}") from exc
        print(
            f"Warning: Z.AI summary request failed; continuing with deterministic report: {exc}",
            file=sys.stderr,
        )
        return None, model

    text = extract_response_text(response_payload)
    if not text:
        message = "Z.AI response did not contain choices[0].message.content"
        if os.getenv("ZAI_REQUIRED", "").strip().lower() in {"1", "true", "yes"}:
            raise RuntimeError(message)
        print(f"Warning: {message}; continuing with deterministic report", file=sys.stderr)
        return None, model
    return text, model


def markdown_title(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")


def build_markdown_report(
    period: str,
    period_name: str,
    slug: str,
    start_date: date,
    end_date: date,
    articles: list[dict[str, str]],
    missing_dates: list[str],
    ai_analysis: str | None,
    ai_model: str | None,
    out_path: Path,
) -> None:
    account_counter = Counter(source_name(article) for article in articles)
    tag_counter: Counter[str] = Counter()
    for article in articles:
        tag_counter.update(parse_tags(article.get("tags", "")))

    lines = [
        f"# 微信公众号文章{period_name}：{slug}",
        "",
        f"- 时间范围：{start_date.isoformat()} 至 {end_date.isoformat()}",
        f"- 文章数量：{len(articles)}",
        f"- 来源数量：{len(account_counter)}",
    ]
    if missing_dates:
        lines.append(f"- 数据提示：缺少 {', '.join(missing_dates)} 的每日 CSV，本报告为不完整汇总")
    lines.append("")

    if not articles:
        lines.append("本期暂无文章。")
    else:
        lines.extend(["## 来源分布", ""])
        lines.extend(f"- {account}: {count} 篇" for account, count in account_counter.most_common())
        lines.append("")

        if tag_counter:
            top_tags = "、".join(f"{tag}({count})" for tag, count in tag_counter.most_common(12))
            lines.extend(["## 高频标签", "", top_tags, ""])

        if ai_analysis:
            lines.extend(["## AI 趋势研判", "", ai_analysis.strip(), ""])
            if ai_model:
                lines.extend(
                    [f"> 由 Z.AI `{ai_model}` 基于本期公开 CSV 中的代表性文章生成。", ""]
                )

        top_articles = sorted(articles, key=article_rank_key, reverse=True)[:30]
        lines.extend(["## 重点文章", ""])
        for idx, article in enumerate(top_articles, 1):
            title = markdown_title(article.get("title", ""))
            url = article.get("url", "")
            title_text = f"[{title}]({url})" if url else title
            summary = article.get("ai_summary") or article.get("digest") or article.get("content_preview")
            lines.extend(
                [
                    f"### {idx}. {title_text}",
                    "",
                    f"- 来源：{source_name(article)}",
                    f"- 发布时间：{article.get('publish_time', '')}",
                ]
            )
            if article.get("tags"):
                lines.append(f"- 标签：{article['tags']}")
            if summary:
                lines.append(f"- 摘要：{make_excerpt(summary, 500)}")
            lines.append("")

        by_day: dict[str, list[dict[str, str]]] = {}
        for article in articles:
            day = article.get("publish_time", "")[:10] or "日期未知"
            by_day.setdefault(day, []).append(article)
        lines.extend(["## 按日期归档", ""])
        for day in sorted(by_day, reverse=True):
            lines.extend([f"### {day}", ""])
            for article in by_day[day]:
                title = markdown_title(article.get("title", ""))
                url = article.get("url", "")
                title_text = f"[{title}]({url})" if url else title
                lines.append(f"- {title_text} - {source_name(article)}")
            lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def generate_period(
    repo_root: Path,
    period: str,
    value: str,
    allow_missing: bool = False,
    today: date | None = None,
) -> tuple[Path, Path]:
    if period == "weekly":
        start_date, end_date, slug = resolve_week_range(value, today=today)
        period_name = "周报"
    elif period == "monthly":
        start_date, end_date, slug = resolve_month_range(value, today=today)
        period_name = "月报"
    else:
        raise ValueError(f"Unsupported period: {period}")

    articles, missing_dates = load_articles(repo_root, start_date, end_date, allow_missing)
    csv_path = repo_root / "csv" / period / f"{slug}.csv"
    report_path = repo_root / "reports" / period / f"{slug}.md"
    write_rollup_csv(articles, csv_path)

    account_counter = Counter(source_name(article) for article in articles)
    tag_counter: Counter[str] = Counter()
    for article in articles:
        tag_counter.update(parse_tags(article.get("tags", "")))
    prompt = build_ai_prompt(
        period_name, slug, start_date, end_date, articles, account_counter, tag_counter
    )
    ai_analysis, ai_model = generate_ai_analysis(prompt)
    build_markdown_report(
        period,
        period_name,
        slug,
        start_date,
        end_date,
        articles,
        missing_dates,
        ai_analysis,
        ai_model,
        report_path,
    )
    return csv_path, report_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    periods: list[str]
    if args.period == "both":
        periods = ["week", "month"]
    elif args.period == "auto":
        today = datetime.now(BEIJING_TZ).date()
        periods = []
        if today.weekday() == 0:
            periods.append("week")
        if today.day == 1:
            periods.append("month")
    else:
        periods = [args.period]

    if not periods:
        print("No periodic summary is due today.")
        return 0

    for period in periods:
        internal_period = "weekly" if period == "week" else "monthly"
        value = args.week if period == "week" else args.month
        csv_path, report_path = generate_period(
            repo_root, internal_period, value, allow_missing=args.allow_missing
        )
        print(f"Generated {internal_period} outputs: {csv_path} and {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
