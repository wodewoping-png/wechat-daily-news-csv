"""Send complete daily channel monitoring results to a DingTalk group robot.

Credentials are read only from the environment; delivery receipts contain no
Webhook, token, signing secret, or raw API response/error message.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BODY_BUDGET = 12000  # conservative UTF-8 message budget, including JSON overhead
RANK = {"critical": 0, "warning": 1, "info": 2}


class DeliveryError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def clean(value):
    # Prevent account names/errors from inserting Markdown links or @ mentions.
    text = " ".join(str(value or "").split()).replace("@", "＠")
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", text)


def validated_webhook(webhook):
    try:
        p = urlsplit(webhook.strip())
        q = parse_qs(p.query)
        valid = (p.scheme == "https" and p.hostname == "oapi.dingtalk.com" and
                 p.path == "/robot/send" and p.port in {None, 443} and
                 not p.username and not p.password and not p.fragment and
                 len(q.get("access_token", [])) == 1 and bool(q["access_token"][0]))
    except ValueError:
        valid = False
    if not valid:
        raise DeliveryError("DINGTALK_WEBHOOK格式无效，需要钉钉群自定义机器人的HTTPS地址。")
    return urlunsplit(("https", "oapi.dingtalk.com", "/robot/send", urlencode({"access_token": q["access_token"][0]}), ""))


def signed_url(webhook, secret, timestamp=None):
    webhook = validated_webhook(webhook)
    if not secret:
        return webhook
    timestamp = str(int(time.time() * 1000)) if timestamp is None else str(timestamp)
    digest = hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()
    return webhook + "&" + urlencode({"timestamp": timestamp, "sign": base64.b64encode(digest).decode()})


def read_snapshot(root):
    folder = root / "quality/channel_monitor"
    report = json.loads((folder / "latest_alerts.json").read_text(encoding="utf-8"))
    as_of = report["as_of"]
    date.fromisoformat(as_of)
    with (folder / "latest.csv").open(encoding="utf-8-sig", newline="") as f:
        channels = list(csv.DictReader(f))
    if any(c["date"] != as_of for c in channels) or len(channels) != report["channel_count"]:
        raise DeliveryError("监测摘要与渠道明细日期或数量不一致，停止发送，避免推送错误日报。")
    events_path = folder / "incident_events.csv"
    recovered = []
    if events_path.exists():
        with events_path.open(encoding="utf-8-sig", newline="") as f:
            recovered = [r for r in csv.DictReader(f) if r["date"] == as_of and r["event"] == "recovered"]
    return report, channels, recovered


def markdown_payload(title, text):
    return {"msgtype": "markdown", "markdown": {"title": title, "text": text}, "at": {"isAtAll": False}}


def build_messages(report, channels, recovered, repo, keyword="wechat", budget=BODY_BUDGET):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise DeliveryError("GitHub仓库标识无效。")
    if not keyword or len(keyword) > 64 or any(c in keyword for c in "\n\r"):
        raise DeliveryError("DINGTALK_KEYWORD需为1至64个字符的单行关键词。")
    # The keyword appears verbatim on every part for robots using keyword security.
    as_of = report["as_of"]
    base = f"https://github.com/{repo}/blob/main"
    n = report.get("observed_articles")
    count = "未知" if n is None else str(n)
    lines = [f"### 每日渠道发文日报 · {as_of}",
             f"- 发文日期：{as_of}（北京时间，非发送日期）",
             f"- 监测渠道：{report['channel_count']}；有文章渠道：{report['observed_active_channels']}",
             f"- 已抓取文章：{count}篇；当前待核查信号：{len(report['alerts'])}条；恢复事件：{len(recovered)}条",
             f"- 每日数据状态：{clean(report['feed_status'])}",
             "> 以下仅表示CSV中已抓到的文章；零观测不等于抓取失败，数据缺失时显示未知。",
             f"[完整检测报告]({base}/quality/channel_monitor/latest.md) · [渠道总表]({base}/channels/channel_summary.csv)",
             "### 异常与处理建议"]
    for a in report["alerts"]:
        lines.append(f"- {clean(a['account_name'] or '全量数据')}（{clean(a['severity'])}）：{clean(a['detail'])}。建议：{clean(a['suggested_action'])}")
    if not report["alerts"]:
        lines.append("- 当前没有达到告警阈值的信号。")
    lines.append("### 当日恢复记录")
    if recovered:
        lines.extend(f"- {clean(r['account_name'] or '全量数据')}：{clean(r['rule'])}不再触发，请结合完整报告核查。" for r in recovered)
    else:
        lines.append("- 当日未记录异常解除事件。")
    lines.append("### 各渠道发文抓取数量（含零观测及未知）")
    for row in sorted(channels, key=lambda r: (RANK.get(r.get("severity"), 2), r["account_name"], r["channel_id"])):
        number = row.get("observed_articles", "")
        number = "未知" if number == "" else str(int(number)) + "篇"
        lines.append(f"- {clean(row['account_name'])}：{number}；{clean(row['status_label'])}；ID {clean(row['channel_id'])}")
    # Reserve enough room for worst-case part numbering. Never silently omit a channel.
    header = f"## {keyword} · {as_of}（9999/9999）\n\n"
    chunks, chunk = [], []
    for line in lines:
        candidate = header + "\n\n".join(chunk + [line])
        size = len(json.dumps(markdown_payload(header, candidate), ensure_ascii=False).encode()) + 100
        if size > budget:
            if not chunk:
                raise DeliveryError("单条渠道信息过长，无法完整发送，请核查监测输入。")
            chunks.append(chunk)
            chunk = [line]
            if len(json.dumps(markdown_payload(header, header + line), ensure_ascii=False).encode()) + 100 > budget:
                raise DeliveryError("单条渠道信息过长，无法完整发送，请核查监测输入。")
        else:
            chunk.append(line)
    if chunk:
        chunks.append(chunk)
    if len(chunks) > 9999:
        raise DeliveryError("消息分段过多，停止发送。")
    return [markdown_payload(f"{keyword} {as_of}（{i}/{len(chunks)}）",
                             f"## {keyword} · {as_of}（{i}/{len(chunks)}）\n\n" + "\n\n".join(chunk))
            for i, chunk in enumerate(chunks, 1)]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(url, payload):
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
                      headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # Never print exception text: it can include the signed URL/access token.
        raise DeliveryError(f"钉钉HTTP请求失败，状态码{exc.code}。", exc.code == 429 or exc.code >= 500) from None
    except (URLError, TimeoutError, OSError):
        raise DeliveryError("钉钉网络请求失败或超时。", True) from None
    except (ValueError, UnicodeError):
        raise DeliveryError("钉钉响应格式无效。", True) from None


def send_part(webhook, secret, payload, message_id, post=post_json, sleep=time.sleep):
    body = {**payload, "msgUuid": message_id}
    for attempt in range(3):
        try:
            result = post(signed_url(webhook, secret), body)
            code = str(result.get("errcode")) if isinstance(result, dict) else "missing"
            if code == "0":
                return
            # Do not log errmsg or raw bodies; they may echo credentials.
            safe_code = code if re.fullmatch(r"-?\d{1,8}", code) else "unknown"
            hints = {"310000": "检查关键词、加签密钥、时间戳或IP白名单。",
                     "400101": "检查Webhook令牌。", "400102": "机器人已停用。",
                     "400106": "机器人不存在，请核对群机器人。",
                     "410100": "触发发送频率限制，请稍后重跑。"}
            raise DeliveryError(f"钉钉拒绝消息，errcode={safe_code}。" + hints.get(code, "请检查机器人配置。"), code in {"-1", "410100"})
        except DeliveryError as exc:
            if not exc.retryable or attempt == 2:
                raise
            sleep(5 * (2 ** attempt))


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def deliver(messages, webhook, secret, state_path, as_of, force_tag="", post=post_json, sleep=time.sleep):
    webhook = validated_webhook(webhook)
    destination = hashlib.sha256(webhook.encode()).hexdigest()
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"delivered": {}}
    sent, skipped = 0, 0
    for index, message in enumerate(messages, 1):
        fingerprint = hashlib.sha256(json.dumps(message, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        message_id = hashlib.sha256(f"{destination}|{as_of}|{fingerprint}|{force_tag}".encode()).hexdigest()
        if message_id in state["delivered"]:
            skipped += 1
            continue
        if sent:
            sleep(3.2)  # below the robot's 20-message/minute limit
        send_part(webhook, secret, message, message_id, post, sleep)
        state["delivered"][message_id] = {"date": as_of, "part": index, "fingerprint": fingerprint}
        cutoff = (date.fromisoformat(as_of) - timedelta(days=90)).isoformat()
        state["delivered"] = {k: v for k, v in state["delivered"].items() if v["date"] >= cutoff}
        # Persist each acknowledged part so a partially failed batch can resume.
        save_state(state_path, state)
        sent += 1
    return sent, skipped


def summary(text):
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as f:
            f.write("\n### 钉钉通知\n\n" + text + "\n")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--dry-run", action="store_true", help="generate a preview without sending")
    p.add_argument("--force", action="store_true", help="resend in a manual workflow run")
    args = p.parse_args(argv)
    webhook = os.getenv("DINGTALK_WEBHOOK", "").strip()
    if not webhook and not args.dry_run:
        summary("DINGTALK_WEBHOOK未配置，本次未发送钉钉消息。")
        return 0
    report, channels, recovered = read_snapshot(args.repo_root)
    messages = build_messages(report, channels, recovered,
                              os.getenv("GITHUB_REPOSITORY", "wodewoping-png/wechat-daily-news-csv"),
                              os.getenv("DINGTALK_KEYWORD", "").strip() or "wechat")
    if args.dry_run:
        path = args.repo_root / "quality/channel_monitor/dingtalk_preview.json"
        path.write_text(json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary(f"预览已生成：{report['as_of']}，共{len(messages)}段；未发送。")
        return 0
    forced = args.force or os.getenv("DINGTALK_FORCE", "").lower() == "true"
    if forced and os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise DeliveryError("强制重发仅允许通过手动workflow_dispatch执行。")
    force_tag = f"manual:{os.environ['GITHUB_RUN_ID']}:{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}" if forced else ""
    sent, skipped = deliver(messages, webhook, os.getenv("DINGTALK_SECRET", "").strip(),
                            args.repo_root / "quality/channel_monitor/dingtalk_delivery_state.json",
                            report["as_of"], force_tag)
    summary(f"钉钉日报 {report['as_of']}：成功发送{sent}段，跳过已发送{skipped}段，共{len(messages)}段。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeliveryError as exc:
        # Only controlled, sanitized errors reach logs.
        summary(str(exc))
        raise SystemExit(1) from None
