from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from scripts.generate_periodic_summary import (
    CSV_COLUMNS,
    extract_response_text,
    generate_ai_analysis,
    generate_period,
    resolve_month_range,
    resolve_week_range,
)


def write_daily_csv(root: Path, target_date: date, index: int) -> None:
    path = root / "csv" / f"{target_date.isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {column: "" for column in CSV_COLUMNS}
    row.update(
        {
            "title": f"文章 {index}",
            "account_name": "测试来源",
            "source": "测试来源",
            "tags": "AI, 测试",
            "priority": "normal",
            "publish_time": f"{target_date.isoformat()}T08:00:00+08:00",
            "digest": f"摘要 {index}",
            "url": f"https://example.com/{index}",
        }
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(row)


class PeriodicSummaryTests(unittest.TestCase):
    def test_resolve_ranges(self) -> None:
        self.assertEqual(
            resolve_week_range("last-week", today=date(2026, 7, 20)),
            (date(2026, 7, 13), date(2026, 7, 19), "2026-W29"),
        )
        self.assertEqual(
            resolve_month_range("last-month", today=date(2026, 7, 20)),
            (date(2026, 6, 1), date(2026, 6, 30), "2026-06"),
        )

    def test_generate_weekly_outputs_from_daily_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            start = date(2026, 7, 13)
            for index in range(7):
                write_daily_csv(root, start + timedelta(days=index), index)
            with mock.patch.dict(os.environ, {"ZAI_API_KEY": ""}):
                csv_path, report_path = generate_period(
                    root, "weekly", "last-week", today=date(2026, 7, 20)
                )

            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            report = report_path.read_text(encoding="utf-8")
            self.assertEqual(len(rows), 7)
            self.assertIn("# 微信公众号文章周报：2026-W29", report)
            self.assertIn("文章数量：7", report)
            self.assertIn("测试来源: 7 篇", report)

    def test_missing_daily_csv_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(FileNotFoundError):
                generate_period(
                    Path(temp_dir), "weekly", "last-week", today=date(2026, 7, 20)
                )

    def test_extract_response_text(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "趋势研判"},
                }
            ]
        }
        self.assertEqual(extract_response_text(payload), "趋势研判")

    def test_zai_request_uses_official_defaults(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self) -> bytes:
                return json.dumps(
                    {"choices": [{"message": {"content": "模型总结"}}]},
                    ensure_ascii=False,
                ).encode("utf-8")

        environment = {
            "ZAI_API_KEY": "test-key",
            "ZAI_BASE_URL": "",
            "ZAI_MODEL": "",
            "ZAI_REQUIRED": "true",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with mock.patch(
                "scripts.generate_periodic_summary.urllib.request.urlopen",
                return_value=FakeResponse(),
            ) as mocked_urlopen:
                text, model = generate_ai_analysis("测试提示")

        request = mocked_urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            request.full_url,
            "https://api.z.ai/api/paas/v4/chat/completions",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(model, "glm-5.1")
        self.assertEqual(text, "模型总结")
        self.assertEqual(body["model"], "glm-5.1")
        self.assertEqual(body["thinking"], {"type": "disabled"})


if __name__ == "__main__":
    unittest.main()
