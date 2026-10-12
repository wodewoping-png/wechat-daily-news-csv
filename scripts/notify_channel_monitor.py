"""Maintain one GitHub issue for active channel incidents; no email/webhook keys needed."""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

MARKER = "<!-- channel-monitor-managed -->"
TITLE = "公众号渠道监测：当前异常与核查清单"


def issue_body(payload, repo):
    base = f"https://github.com/{repo}/blob/main"
    lines = [MARKER, f"监测发文日期：{payload['as_of']}（北京时间）", "",
             f"待核查信号：{len(payload['alerts'])}；渠道：{payload['channel_count']}；数据状态：{payload['feed_status']}", "",
             "零发文/数量骤降/长期沉默是疑似信号，不能直接证明抓取失败。明确的抓取失败来自抓取器最终事件。", "",
             f"[完整监测报告]({base}/quality/channel_monitor/latest.md) · [渠道总表]({base}/channels/channel_summary.csv) · [异常和恢复记录]({base}/quality/channel_monitor/incident_events.csv)", ""]
    for a in payload["alerts"][:100]:
        lines += [f"### {a['severity']} · {a['account_name'] or '全量数据'}", a["detail"],
                  "处理建议：" + a["suggested_action"], ""]
    if len(payload["alerts"]) > 100:
        lines.append("其余信号见完整监测报告。")
    if not payload["alerts"]:
        lines.append("当前没有达到告警阈值的信号，已关闭本期异常清单。")
    return "\n".join(lines)


def sync_issue(payload, repo, api):
    # api(method, path, body) is injected in tests; only the managed issue is modified.
    existing = None
    for page in range(1, 11):
        issues = api("GET", f"/repos/{repo}/issues?state=open&per_page=100&page={page}", None)
        existing = next((i for i in issues if not i.get("pull_request") and MARKER in (i.get("body") or "")), None)
        if existing or len(issues) < 100:
            break
    body = issue_body(payload, repo)
    if payload["alerts"]:
        if existing:
            if existing.get("body") != body:
                api("PATCH", f"/repos/{repo}/issues/{existing['number']}", {"body": body})
        else:
            api("POST", f"/repos/{repo}/issues", {"title": TITLE, "body": body})
    elif existing:
        api("PATCH", f"/repos/{repo}/issues/{existing['number']}", {"body": body, "state": "closed"})


def main():
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "quality/channel_monitor/latest_alerts.json").read_text(encoding="utf-8"))
    token = os.environ["GH_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")

    def api(method, path, data):
        request = Request(base + path, method=method,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    sync_issue(payload, repo, api)
    print("Managed channel-monitor issue synchronized.")


if __name__ == "__main__":
    main()
