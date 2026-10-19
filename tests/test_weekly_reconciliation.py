import csv
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.reconcile_weekly_summaries import reconcile

REPO = Path(__file__).resolve().parents[1]


class WeeklyReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'config').mkdir()
        (self.root / 'csv').mkdir()
        for file in ['industry_classification.json', 'industry_taxonomy.json']:
            (self.root / 'config' / file).write_bytes((REPO / 'config' / file).read_bytes())

    def source(self, day, body='完整正文'):
        with (self.root / 'csv' / f'{day}.csv').open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['title', 'account_name', 'url', 'clean_text', 'publish_time'])
            w.writeheader()
            w.writerow({'title': f'新闻{day}', 'account_name': '测试', 'url': f'https://example.com/{day}',
                        'clean_text': body, 'publish_time': f'{day}T12:00:00+08:00'})

    def test_late_sunday_publication_generates_week_without_waiting_for_clock(self):
        start = date(2026, 10, 12)
        for i in range(6):
            self.source(start + timedelta(days=i))
        with patch('scripts.generate_periodic_summary.generate_ai_analysis', side_effect=AssertionError('周合并不能等待API')):
            generated, missing = reconcile(self.root, today=date(2026, 10, 19))
            self.assertEqual(generated, [])
            self.assertEqual(missing[0]['missing_dates'], ['2026-10-18'])
            self.source(date(2026, 10, 18), '长正文' * 50000)
            self.assertEqual(reconcile(self.root, today=date(2026, 10, 19))[0], ['2026-W42'])
            self.assertEqual(reconcile(self.root, today=date(2026, 10, 19))[0], [])
            self.source(date(2026, 10, 18), '修订正文')
            self.assertEqual(reconcile(self.root, today=date(2026, 10, 19))[0], ['2026-W42'])
        for file in ['csv/weekly/2026-W42.csv', 'reports/weekly/2026-W42.md', 'classified/weekly/2026-W42.csv']:
            self.assertTrue((self.root / file).exists())
        meta = json.loads((self.root / 'classified/weekly/2026-W42.json').read_text())
        self.assertEqual(meta['articles'], 7)
        self.assertTrue(meta['data_complete'])
        self.assertFalse(meta['classification_complete'])

    def test_new_workflows_are_source_driven_and_downstream_runs_after_summary(self):
        periodic = (REPO / '.github/workflows/periodic-summary.yml').read_text()
        trend = (REPO / '.github/workflows/cross-repo-ai-trend-summary.yml').read_text()
        self.assertIn('workflows: [channel-monitor]', periodic)
        self.assertIn('python scripts/reconcile_weekly_summaries.py', periodic)
        self.assertIn('git add -A', periodic)
        self.assertIn('classified/weekly', periodic)
        self.assertIn('workflows: [periodic-summary]', trend)


if __name__ == '__main__':
    unittest.main()
