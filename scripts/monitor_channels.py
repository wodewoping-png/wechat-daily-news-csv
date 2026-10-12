"""Inventory and evidence-based monitoring of the published daily feed.

All counts mean articles observed in this repository, not all posts on WeChat.
The script is deterministic for a given target date and set of input files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
LABELS = {
    "observed": "正常：已抓到文章", "zero_observed": "当天未抓到文章",
    "never_observed": "名录渠道尚未观测", "suspected_silence": "疑似渠道中断",
    "volume_drop": "疑似发文抓取量骤降", "fetch_error": "确认抓取失败",
    "partial_fetch_error": "抓到部分文章但抓取报错", "not_configured": "未配置抓取",
    "unknown": "数据不完整，无法判断", "disabled": "已停用监测",
}
RANK = {"critical": 3, "warning": 2, "info": 1}
DEFAULTS = {"history_days": 56, "baseline_days": 28, "minimum_baseline_days": 14,
            "minimum_silence_days": 3, "never_observed_days": 28,
            "volume_drop_ratio": .2, "minimum_volume_baseline": 3, "overrides": {}}


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def channel_id(fakeid, name):
    return "wx:" + fakeid if fakeid else "name:" + hashlib.sha256(normalized(name).encode()).hexdigest()[:16]


def url_identity(value):
    """Ignore WeChat tracking query fields while preserving article identity."""
    p = urlsplit(value.strip())
    q = parse_qs(p.query)
    biz = q.get("__biz", [""])[0] if p.hostname == "mp.weixin.qq.com" else ""
    if biz and q.get("mid") and q.get("idx"):
        return "wx:" + "|".join((biz, q["mid"][0], q["idx"][0])), biz
    q = {k: v for k, v in q.items() if k not in {"scene", "from", "isappinstalled", "chksm"} and not k.startswith("utm_")}
    return urlunsplit(("https" if p.scheme in {"http", "https"} else p.scheme,
                       p.netloc.lower(), p.path, urlencode(q, doseq=True), "")), biz


def publication_day(value, fallback):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (dt.astimezone(TZ) if dt.tzinfo else dt).date()
    except (ValueError, AttributeError):
        return fallback


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def alert(rule, severity, detail, cid="", name="", action=""):
    return {"key": cid + "|" + rule, "channel_id": cid, "account_name": name,
            "rule": rule, "severity": severity, "detail": detail, "suggested_action": action}


def read_inputs(root, target):
    registry, by_name, by_biz = {}, defaultdict(set), {}
    identity_alerts = []

    def add(name, biz="", alias="", listed="", source=""):
        cid = channel_id(biz, name)
        if cid not in registry:
            registry[cid] = {"channel_id": cid, "account_name": name, "seed_name": name if listed else "",
                             "fakeid": biz, "wechat_alias": alias, "listed_since": listed,
                             "origin": "名单+每日CSV" if listed else "每日CSV发现", "source_reference": source,
                             "aliases": [], "first_observed": "", "last_observed": "", "sample_url": "",
                             "source_type": "微信公众号" if biz or listed else "待识别信息源"}
        by_name[normalized(name)].add(cid)
        if biz:
            by_biz[biz] = cid
        return cid

    with (root / "channels/seed_accounts.csv").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("account_name"):
                add(row["account_name"], row.get("fakeid", ""), row.get("wechat_alias", ""),
                    row.get("listed_since", ""), row.get("source_reference", ""))

    counts, totals, feed = defaultdict(Counter), {}, {}
    duplicate_counts = defaultdict(Counter)
    row_errors = defaultdict(Counter)
    seen_articles = set()
    for path in sorted((root / "csv").glob("????-??-??.csv")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day > target:
            continue
        status = {"status": "ok", "rows": 0, "unique_articles": 0, "errors": 0,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        try:
            with path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if not {"title", "publish_time", "url"}.issubset(reader.fieldnames or []) or not {"account_name", "source"}.intersection(reader.fieldnames or []):
                    raise ValueError("每日CSV缺少必要列")
                for row in reader:
                    status["rows"] += 1
                    name = (row.get("account_name") or row.get("source") or "").strip()
                    if not name or None in row:
                        status["errors"] += 1
                        continue
                    raw_url = (row.get("url") or "").strip()
                    try:
                        identity, biz = url_identity(raw_url)
                    except ValueError:
                        identity, biz = raw_url, ""
                        status["errors"] += 1
                    candidates = by_name[normalized(name)]
                    if biz and biz in by_biz:
                        cid = by_biz[biz]
                    elif len(candidates) == 1:
                        existing = next(iter(candidates))
                        old_biz = registry[existing]["fakeid"]
                        if biz and old_biz and biz != old_biz:
                            cid = add(name, biz)
                            identity_alerts.append(alert("identity_conflict", "warning", "同名渠道出现不同fakeid，已保留独立记录，未自动合并", cid, name,
                                                        "核查名录fakeid与同名公众号，确认后修正seed_accounts.csv中的名称或fakeid"))
                        else:
                            cid = existing
                            if biz and not old_biz:
                                registry[cid]["fakeid"] = biz
                                registry[cid]["source_type"] = "微信公众号"
                                by_biz[biz] = cid
                    else:
                        cid = add(name, biz)
                        if len(candidates) > 1 and not biz:
                            identity_alerts.append(alert("identity_conflict", "warning", "同名渠道缺少fakeid，无法可靠归属", cid, name, "补充原始公众号链接或fakeid"))
                    entry = registry[cid]
                    if name not in entry["aliases"]:
                        entry["aliases"].append(name)
                    by_name[normalized(name)].add(cid)
                    entry["account_name"] = name
                    entry["first_observed"] = entry["first_observed"] or day.isoformat()
                    entry["last_observed"] = day.isoformat()
                    entry["sample_url"] = raw_url or entry["sample_url"]
                    key = (cid, identity or "\x1f".join((row.get("title", ""), row.get("publish_time", ""))))
                    if key in seen_articles:
                        duplicate_counts[day][cid] += 1
                        continue
                    seen_articles.add(key)
                    if publication_day(row.get("publish_time", ""), day) != day:
                        row_errors[day][cid] += 1
                    counts[day][cid] += 1
                    status["unique_articles"] += 1
            if not status["rows"]:
                status["status"] = "empty"
            elif status["errors"]:
                status["status"] = "invalid"
        except (UnicodeError, csv.Error, ValueError) as exc:
            status["status"] = "invalid"
            status["error"] = str(exc)
        feed[day] = status
        totals[day] = status["unique_articles"]
    for entry in registry.values():
        entry["aliases"].sort()
        entry["tracked_from"] = min(x for x in (entry["listed_since"], entry["first_observed"]) if x)
        if entry["listed_since"] and not entry["first_observed"]:
            entry["origin"] = "上传名单，尚未观测"
    return registry, counts, totals, feed, duplicate_counts, row_errors, identity_alerts


def telemetry(root, day, registry):
    path = root / "quality/channel_fetch_events" / f"{day}.jsonl"
    results, errors = {}, []
    if not path.exists():
        return results, errors
    # Records must be chronological; the final attempt determines final status.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("status") not in {"success", "error", "not_configured"}:
                raise ValueError("无效状态")
            cid = row.get("channel_id")
            if cid not in registry:
                matches = [k for k, v in registry.items() if
                           (row.get("fakeid") and row["fakeid"] == v["fakeid"]) or
                           (row.get("account_name") and normalized(row["account_name"]) in {normalized(n) for n in v["aliases"] + [v["seed_name"]]})]
                if len(matches) != 1:
                    raise ValueError("渠道标识不存在或有歧义")
                cid = matches[0]
            # Use structured error codes only, never publish raw credential-bearing logs.
            code = re.sub(r"[^a-zA-Z0-9_-]", "", str(row.get("error_code", "")))[:48]
            results[cid] = {"status": row["status"], "error_code": code}
        except (ValueError, TypeError):
            errors.append(alert("telemetry_invalid", "warning", "抓取事件记录格式或渠道标识无效", action="核查channel_fetch_events输入契约"))
    return results, errors


def assess(entry, day, counts, feed, cfg, events):
    cid, n = entry["channel_id"], counts[day][entry["channel_id"]]
    override = cfg["overrides"].get(cid, {})
    start = date.fromisoformat(entry["tracked_from"])
    prior = [d for d in sorted(feed) if start <= d < day and
             d >= day - timedelta(days=cfg["baseline_days"]) and feed[d]["status"] == "ok"]
    active = [d for d in sorted(feed) if start <= d < day and feed[d]["status"] == "ok" and counts[d][cid] > 0][-10:]
    same_kind = [d for d in prior if (d.weekday() >= 5) == (day.weekday() >= 5)]
    baseline = statistics.median([counts[d][cid] for d in same_kind]) if len(same_kind) >= 4 else 0
    gaps = [(b - a).days for a, b in zip(active, active[1:])]
    expected = float(override.get("expected_interval_days", statistics.median(gaps) if gaps else 0))
    warn_days = int(override.get("silence_warn_days", max(cfg["minimum_silence_days"], math.ceil(expected * 3))))
    crit_days = int(override.get("silence_critical_days", max(14, warn_days * 2)))
    last = max((d for d in feed if d <= day and counts[d][cid]), default=None)
    silence = 0
    cursor = day
    while cursor >= start and feed.get(cursor, {}).get("status") == "ok" and not counts[cursor][cid]:
        silence += 1
        cursor -= timedelta(days=1)
    row = {"date": day.isoformat(), "channel_id": cid, "account_name": entry["account_name"],
           "observed_articles": n, "baseline_median": baseline, "baseline_sample_days": len(prior),
           "consecutive_observed_zero_days": silence, "last_observed_date": last.isoformat() if last else "",
           "expected_interval_days": expected, "silence_warn_days": warn_days,
           "status": "observed" if n else "zero_observed", "severity": "info", "evidence": "CSV发文日期分组",
           "suggested_action": ""}
    alerts = []
    if not override.get("enabled", True):
        row["status"] = "disabled"
    elif feed.get(day, {}).get("status") != "ok":
        row.update(status="unknown", observed_articles="", evidence="每日数据缺失、为空或格式无效")
    else:
        event = events.get(cid, {})
        if event.get("status") in {"error", "not_configured"}:
            status = "partial_fetch_error" if n and event["status"] == "error" else "fetch_error" if event["status"] == "error" else "not_configured"
            severity = "warning" if n else "critical"
            row.update(status=status, severity=severity, evidence="抓取器最终事件：" + event["status"] + " " + event.get("error_code", ""),
                       suggested_action="检查渠道配置、登录凭据有效性、接口限流及重试日志；修复后补抓并重新运行监测")
        elif not last:
            row["status"] = "never_observed"
            days = len([d for d in feed if start <= d <= day and feed[d]["status"] == "ok"])
            if entry["listed_since"] and days >= cfg["never_observed_days"]:
                row.update(severity="warning", evidence=f"名单渠道在{days}个完整数据日均未观测；不能据此证明抓取失败",
                           suggested_action="核查是否已订阅/启用，fakeid与名称是否匹配，并人工查看最近发文")
        elif len(prior) >= cfg["minimum_baseline_days"] and (len(active) >= 7 or override.get("expected_interval_days")) and silence >= warn_days:
            row.update(status="suspected_silence", severity="critical" if silence >= crit_days else "warning",
                       evidence=f"连续{silence}个完整数据日未抓到文章，历史发文间隔中位数{expected:g}天；原因待核查",
                       suggested_action="先核对公众号实际发文，排除节假日/停更；若实际有文章，检查渠道配置与接口并补抓")
        elif len(prior) >= cfg["minimum_baseline_days"] and baseline >= cfg["minimum_volume_baseline"] and n < baseline * cfg["volume_drop_ratio"]:
            row.update(status="volume_drop", severity="warning", evidence=f"当天抓到{n}篇，同类工作日/周末基线中位数{baseline:g}篇；原因待核查",
                       suggested_action="对照公众号当天实际发文，核查翻页、去重、时间范围及数据源响应")
        if row["severity"] != "info":
            alerts.append(alert(row["status"], row["severity"], row["evidence"], cid, entry["account_name"], row["suggested_action"]))
    return row, alerts


def incident_history(root, target, alerts, current, events):
    state_path = root / "quality/channel_monitor/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"active": {}, "events": []}
    if state.get("as_of", "") > target.isoformat():
        raise ValueError("不能用较早日期覆盖当前监测状态；历史回放请使用独立repo-root")
    active = {a["key"]: a for a in alerts}
    current_by_id = {r["channel_id"]: r for r in current}
    for key, a in active.items():
        old = state["active"].get(key)
        a["first_detected"] = old.get("first_detected", target.isoformat()) if old else target.isoformat()
        if old is None or old["severity"] != a["severity"]:
            state["events"].append({"date": target.isoformat(), "event": "opened" if old is None else "severity_changed", **a})
    for key, old in state["active"].items():
        if key not in active:
            # With a missing entire feed, individual incidents cannot be declared recovered.
            cid = old["channel_id"]
            unverified_error = (old["rule"] in {"fetch_error", "partial_fetch_error", "not_configured"}
                                and not current_by_id.get(cid, {}).get("observed_articles")
                                and current_by_id.get(cid, {}).get("status") != "disabled"
                                and events.get(cid, {}).get("status") != "success")
            if cid and (any(a["rule"] == "daily_feed_unavailable" for a in alerts) or unverified_error):
                active[key] = old
                continue
            state["events"].append({"date": target.isoformat(), "event": "recovered", **old})
    state.update(as_of=target.isoformat(), active=active)
    write_json(state_path, state)
    write_csv(root / "quality/channel_monitor/incident_events.csv", state["events"],
              ["date", "event", "key", "channel_id", "account_name", "rule", "severity", "first_detected", "detail", "suggested_action"])
    return list(active.values())


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def run(root, target):
    cfg = {**DEFAULTS, **json.loads((root / "channels/monitor_config.json").read_text(encoding="utf-8"))}
    registry, counts, totals, feed, duplicates, bad_dates, identity_alerts = read_inputs(root, target)
    history, current, alerts = [], [], []
    if feed.get(target, {}).get("status") != "ok":
        alerts.append(alert("daily_feed_unavailable", "critical", f"{target}每日CSV缺失、为空或格式无效；无法判断各渠道发文状态", action="检查上游每日发布任务并补齐有效CSV，随后重跑监测"))
    else:
        prior = [d for d in feed if target - timedelta(days=28) <= d < target and feed[d]["status"] == "ok" and (d.weekday() >= 5) == (target.weekday() >= 5)]
        baseline = statistics.median([totals[d] for d in prior]) if len(prior) >= 4 else 0
        if baseline >= 50 and totals[target] < baseline * .2:
            alerts.append(alert("aggregate_volume_drop", "warning", f"全量仅{totals[target]}篇，同类日期基线{baseline:g}篇；可能存在全局漏抓", action="优先检查上游接口、认证和合并流程，再核查单渠道"))
    alerts.extend({a["key"]: a for a in identity_alerts}.values())
    beginning = min(min(feed, default=target), target - timedelta(days=cfg["history_days"] - 1))
    for day in (beginning + timedelta(days=i) for i in range((target - beginning).days + 1)):
        events, errors = telemetry(root, day, registry)
        if day == target:
            alerts.extend(errors)
        for cid, entry in sorted(registry.items()):
            if day.isoformat() < entry["tracked_from"]:
                continue
            row, found = assess(entry, day, counts, feed, cfg, events)
            row.update(duplicate_articles=duplicates[day][cid], publish_date_mismatches=bad_dates[day][cid],
                       status_label=LABELS[row["status"]])
            history.append(row)
            if day == target:
                current.append(row)
                alerts.extend(found)
    if sum(duplicates[target].values()):
        alerts.append(alert("duplicate_articles", "warning", f"每日文件中有{sum(duplicates[target].values())}条重复文章，发文统计已去重", action="核查上游URL去重与分页重叠"))
    if sum(bad_dates[target].values()):
        alerts.append(alert("publish_date_mismatch", "warning", f"有{sum(bad_dates[target].values())}条文章发布时间不属于文件日期", action="核查时区与每日导出时间范围"))
    alerts = sorted({a["key"]: a for a in alerts}.values(), key=lambda a: (-RANK[a["severity"]], a["account_name"], a["rule"]))
    target_events, _ = telemetry(root, target, registry)
    alerts = incident_history(root, target, alerts, current, target_events)
    alerts.sort(key=lambda a: (-RANK[a["severity"]], a["account_name"], a["rule"]))
    latest = {r["channel_id"]: r for r in current}
    catalog = []
    for cid, entry in sorted(registry.items(), key=lambda kv: kv[1]["account_name"]):
        row = latest.get(cid, {})
        open_incidents = [a for a in alerts if a["channel_id"] == cid]
        severity = max([row.get("severity", "info")] + [a["severity"] for a in open_incidents], key=lambda s: RANK[s])
        catalog.append({**entry, "aliases": "；".join(entry["aliases"]), "as_of": target.isoformat(),
                        "total_observed_articles": sum(c[cid] for c in counts.values()),
                        "observed_articles_7d": sum(counts[target-timedelta(days=i)][cid] for i in range(7)),
                        "observed_articles_28d": sum(counts[target-timedelta(days=i)][cid] for i in range(28)),
                        "latest_status": row.get("status_label", "尚未开始监测"), "latest_severity": severity,
                        "open_incident_count": len(open_incidents), "open_incident_rules": "；".join(a["rule"] for a in open_incidents),
                        "enabled": cfg["overrides"].get(cid, {}).get("enabled", True)})
    write_json(root / "channels/registry.json", {"as_of": target.isoformat(), "channels": list(registry.values())})
    headings = {
        "channel_id": "渠道ID", "account_name": "当前公众号名称", "seed_name": "名单原名称",
        "fakeid": "fakeid", "wechat_alias": "微信号", "source_type": "信息源类型", "origin": "纳入方式",
        "aliases": "历史名称", "tracked_from": "开始监测日期", "first_observed": "首次抓到文章日期",
        "last_observed": "最近抓到文章日期", "total_observed_articles": "累计已观测文章数",
        "observed_articles_7d": "近7日已观测文章数", "observed_articles_28d": "近28日已观测文章数",
        "latest_status": "最新检测状态", "latest_severity": "告警级别", "enabled": "启用监测",
        "open_incident_count": "未解除问题数", "open_incident_rules": "未解除问题类型",
        "as_of": "统计截至日期", "listed_since": "名单生效日期", "sample_url": "最近文章示例链接", "source_reference": "来源依据"
    }
    table = [{label: c.get(key, "") for key, label in headings.items()} for c in catalog]
    write_csv(root / "channels/channel_summary.csv", table, list(headings.values()))
    fields = ["date", "channel_id", "account_name", "observed_articles", "status_label", "severity", "last_observed_date",
              "consecutive_observed_zero_days", "baseline_median", "baseline_sample_days", "expected_interval_days", "silence_warn_days",
              "duplicate_articles", "publish_date_mismatches", "evidence", "suggested_action", "status"]
    write_csv(root / "quality/channel_monitor/daily_history.csv", history, fields)
    write_csv(root / "quality/channel_monitor/latest.csv", current, fields)
    write_csv(root / "quality/channel_monitor/feed_manifest.csv", [{"date": d.isoformat(), **s} for d, s in sorted(feed.items())],
              ["date", "status", "rows", "unique_articles", "errors", "sha256", "error"])
    payload = {"as_of": target.isoformat(), "feed_status": feed.get(target, {}).get("status", "missing"), "channel_count": len(catalog),
               "observed_active_channels": sum(bool(r["observed_articles"]) for r in current),
               "observed_articles": totals.get(target), "alerts": alerts}
    write_json(root / "quality/channel_monitor/latest_alerts.json", payload)
    lines = [f"# 公众号渠道每日监测：{target}", "", f"- 渠道总数：{len(catalog)}（名单渠道 {sum(bool(c['listed_since']) for c in catalog)}）",
             f"- 当天已抓到文章：{totals.get(target, '未知')}；有文章的渠道：{payload['observed_active_channels']}",
             f"- 当前待核查问题：{len(alerts)}；全量数据状态：{payload['feed_status']}", "",
             "> 发文数量是每日CSV中已观测的文章数，不代表公众号实际全部发文。CSV缺失时显示未知，不能当作零发文。",
             "> 无抓取器事件时，零文章、骤降和长时间沉默仅为待核查信号；节假日或真实停更也可能造成这些信号。", "",
             "[渠道总表](../../channels/channel_summary.csv) · [当前逐渠道状态](latest.csv) · [逐日记录](daily_history.csv) · [异常/恢复记录](incident_events.csv)", "",
             "## 当前异常与处理建议", "", "|级别|渠道|信号|建议|", "|---|---|---|---|"]
    lines += ["|" + "|".join(cell(x) for x in (a["severity"], a["account_name"] or "全量数据", a["detail"], a["suggested_action"])) + "|" for a in alerts]
    if not alerts:
        lines.append("|正常|全部|未检测到达到告警阈值的信号|继续监测|")
    lines += ["", "## 更新与核查", "", "渠道按fakeid或规范化名称识别，新渠道自动加入总表；同名但fakeid不同的记录独立保留。",
              "核查后可在 `channels/monitor_config.json` 的 overrides 中按 channel_id 停用渠道或设置发文间隔/沉默阈值。",
              "修复上游后重新发布每日CSV，并运行 channel-monitor；异常解除会记录恢复事件。"]
    report = "\n".join(lines) + "\n"
    out = root / "quality/channel_monitor/latest.md"
    out.write_text(report, encoding="utf-8")
    (root / "quality/channel_monitor/daily").mkdir(parents=True, exist_ok=True)
    # Daily report links need one additional level relative to latest.md.
    (root / "quality/channel_monitor/daily" / f"{target}.md").write_text(report.replace("../../channels/", "../../../channels/").replace("(latest.csv)", "(../latest.csv)").replace("(daily_history.csv)", "(../daily_history.csv)").replace("(incident_events.csv)", "(../incident_events.csv)"), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "alerts"}, ensure_ascii=False))
    return payload


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--date", default="yesterday", help="publish date YYYY-MM-DD, defaults to yesterday in Beijing")
    args = p.parse_args(argv)
    target = datetime.now(TZ).date() - timedelta(days=1) if args.date == "yesterday" else date.fromisoformat(args.date)
    if target >= datetime.now(TZ).date():
        p.error("只监测已结束的日期，避免将当天尚未发文误判为异常")
    run(args.repo_root.resolve(), target)


if __name__ == "__main__":
    main()
