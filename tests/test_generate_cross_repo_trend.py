from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from scripts.generate_cross_repo_trend import (
    call_zai,
    generate_one,
    load_news_monthly,
    resolve_month,
    resolve_week,
)


TEMPLATE = """周期={{PERIOD_TYPE}}/{{PERIOD_SLUG}}
微信={{WECHAT_SUMMARY}}
新闻={{NEWS_SUMMARY}}
"""


def write_weekly_fixture(wechat_root: Path, news_root: Path, template_path: Path) -> None:
    wechat_path = wechat_root / "reports" / "weekly" / "2026-W29.md"
    wechat_path.parent.mkdir(parents=True, exist_ok=True)
    wechat_path.write_text(
        "# 微信周报\n\n## 来源分布\n\n- 来源甲: 2 篇\n\n## 重点文章\n\n文章甲\n\n## 按日期归档\n\n归档内容",
        encoding="utf-8",
    )
    news_path = news_root / "data" / "weekly" / "articles_week_2026-W29.csv"
    news_path.parent.mkdir(parents=True, exist_ok=True)
    with news_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["title", "published_at", "content", "source_name", "domain", "sub_domain"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "title": "网页文章",
                "published_at": "2026-07-15",
                "content": "网页摘要",
                "source_name": "新闻来源",
                "domain": "能源",
                "sub_domain": "储能",
            }
        )
    template_path.write_text(TEMPLATE, encoding="utf-8")


class CrossRepoTrendTests(unittest.TestCase):
    def test_resolve_periods(self) -> None:
        self.assertEqual(
            resolve_week("last-week", today=date(2026, 7, 20)),
            (date(2026, 7, 13), date(2026, 7, 19), "2026-W29"),
        )
        self.assertEqual(
            resolve_month("last-month", today=date(2026, 7, 20)),
            (date(2026, 6, 1), date(2026, 6, 30), "2026-06"),
        )

    def test_generate_weekly_report_with_mocked_zai(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wechat_root = root / "wechat"
            news_root = root / "news"
            template_path = root / "template.md"
            write_weekly_fixture(wechat_root, news_root, template_path)

            with mock.patch(
                "scripts.generate_cross_repo_trend.call_zai",
                return_value=("### 总体判断\n\n测试趋势", "glm-5.2"),
            ):
                output = generate_one(
                    "week",
                    "2026-W29",
                    date(2026, 7, 13),
                    date(2026, 7, 19),
                    wechat_root,
                    news_root,
                    template_path,
                )

            self.assertIsNotNone(output)
            report = output.read_text(encoding="utf-8")
            self.assertIn("跨来源 AI 趋势周度报告：2026-W29", report)
            self.assertIn("测试趋势", report)
            self.assertIn("glm-5.2", report)

    def test_monthly_workbook_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "month.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Articles"
            sheet.append(["title", "published_at", "content", "source_name", "domain", "sub_domain"])
            sheet.append(["月度文章", "2026-06-01", "摘要", "来源乙", "氢能", "制氢"])
            workbook.save(path)
            summary = load_news_monthly(path)
            self.assertIn("月度文章", summary)
            self.assertIn("氢能", summary)

    def test_zai_call_uses_requested_glm_5_2(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": "趋势"}}]}).encode("utf-8")

        environment = {
            "ZAI_API_KEY": "test-key",
            "ZAI_BASE_URL": "",
            "ZAI_MODEL": "glm-5.2",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with mock.patch(
                "scripts.generate_cross_repo_trend.urllib.request.urlopen",
                return_value=FakeResponse(),
            ) as mocked_urlopen:
                text, model = call_zai("prompt")

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.z.ai/api/paas/v4/chat/completions")
        self.assertEqual(payload["model"], "glm-5.2")
        self.assertEqual(model, "glm-5.2")
        self.assertEqual(text, "趋势")


if __name__ == "__main__":
    unittest.main()
