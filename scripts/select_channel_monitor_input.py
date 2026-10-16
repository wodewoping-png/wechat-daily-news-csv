"""Select monitoring only for a real daily-CSV publication (or manual run)."""
import json
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

try:
    from scripts.monitor_channels import TZ, latest_csv_date
except ModuleNotFoundError:
    from monitor_channels import TZ, latest_csv_date


def select_input(root, changed_paths, event_name, requested="latest", today=None):
    today = today or datetime.now(TZ).date()
    if event_name == "workflow_dispatch":
        target = latest_csv_date(root, today) if requested == "latest" else date.fromisoformat(requested)
        if target >= today or not (root / "csv" / f"{target}.csv").is_file():
            raise ValueError("手动监测日期必须已结束且相应每日CSV已经入库")
        return {"monitor": "true", "date": target.isoformat()}
    if event_name != "push":
        return {"monitor": "false", "date": ""}
    published = []
    for path in changed_paths:
        if re.fullmatch(r"csv/\d{4}-\d{2}-\d{2}\.csv", path):
            try:
                day = date.fromisoformat(Path(path).stem)
            except ValueError:
                continue
            if day < today and (root / path).is_file():
                published.append(day)
    # Late publication/backfills use the latest available completed CSV, never a
    # clock-derived 'yesterday' that might not exist when the job finally starts.
    return {"monitor": "true", "date": latest_csv_date(root, today).isoformat()} if published else {"monitor": "false", "date": ""}


def changed_files(root, before, after):
    if not all(re.fullmatch(r"[0-9a-f]{40}", s or "") for s in (before, after)):
        raise ValueError("GitHub push提交标识无效")
    for sha in (before, after):
        if sha == "0" * 40:
            continue
        exists = subprocess.run(["git", "cat-file", "-e", sha], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if exists.returncode:
            subprocess.run(["git", "fetch", "--no-tags", "--depth=1", "origin", sha], cwd=root, check=True)
    cmd = (["git", "ls-tree", "-r", "--name-only", after] if before == "0" * 40 else
           ["git", "diff", "--name-only", "--diff-filter=AM", "--no-renames", before, after])
    return subprocess.check_output(cmd, cwd=root, text=True).splitlines()


def main():
    root = Path(__file__).resolve().parents[1]
    event_name = os.environ["GITHUB_EVENT_NAME"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    paths = changed_files(root, event["before"], event["after"]) if event_name == "push" else []
    result = select_input(root, paths, event_name, (event.get("inputs") or {}).get("date") or "latest")
    result["refresh"] = "false"
    if event_name == "push" and {"channels/monitor_config.json", "channels/seed_accounts.csv"}.intersection(paths) and result["monitor"] == "false":
        result.update(refresh="true", date=latest_csv_date(root).isoformat())
    published = [p for p in paths if re.fullmatch(r"csv/\d{4}-\d{2}-\d{2}\.csv", p) and (root / p).is_file()]
    if event_name == "workflow_dispatch":
        published = [f"csv/{result['date']}.csv"]
    result["csv_paths"] = json.dumps(published)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as f:
        f.writelines(f"{key}={value}\n" for key, value in result.items())
    print("每日CSV已入库，监测发文日期 " + result["date"] if result["monitor"] == "true" else
          "渠道配置变更：刷新已有统计，不发钉钉消息。" if result["refresh"] == "true" else "非每日CSV入库事件：仅维护监控表，不监测、不发钉钉消息。")


if __name__ == "__main__":
    main()
