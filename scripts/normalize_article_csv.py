"""Normalize newly published article CSVs, retaining old archives unchanged."""
import csv
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

csv.field_size_limit(20 * 1024 * 1024)


def normalize_csv(path, excluded_fakeids=()):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        if not fields:
            raise ValueError("每日CSV没有表头")
        rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError("每日CSV行与表头不匹配，未改写")
    out_fields = [k for k in fields if k != "digest"]
    if "digest" in fields and "clean_text" not in out_fields:
        out_fields.append("clean_text")
    output = []
    removed = 0
    for row in rows:
        try:
            p = urlsplit(row.get("url") or "")
            biz = (parse_qs(p.query).get("__biz") or [""])[0] if p.hostname == "mp.weixin.qq.com" else ""
        except ValueError:
            biz = ""
        if (row.get("account_fakeid") or biz) in excluded_fakeids:
            removed += 1
            continue
        if "digest" in fields:
            row["clean_text"] = row.get("clean_text") or row.get("digest") or ""
        output.append({k: row.get(k, "") for k in out_fields})
    if "digest" not in fields and not removed:
        return False
    temporary = path.with_suffix(".normalize.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(output)
    temporary.replace(path)
    return True


def main():
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "channels/monitor_config.json").read_text(encoding="utf-8"))
    paths = json.loads(os.environ.get("PUBLISHED_CSV_PATHS", "[]"))
    for path in paths:
        if not re.fullmatch(r"csv/\d{4}-\d{2}-\d{2}\.csv", path):
            raise ValueError("只允许规范化已入库的每日CSV")
        changed = normalize_csv(root / path, set(cfg.get("excluded_fakeids", [])))
        print(f"{path}: {'已移除digest/排除指定误抓取渠道' if changed else '已符合输出格式'}")


if __name__ == "__main__":
    main()
