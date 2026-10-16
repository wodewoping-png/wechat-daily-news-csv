import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.select_channel_monitor_input import changed_files, select_input


class PublicationTriggerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'csv').mkdir()
        (self.root / 'csv/2026-10-14.csv').write_text('generated csv')
        self.today = date(2026, 10, 16)

    def select(self, paths, event='push', requested='latest'):
        return select_input(self.root, paths, event, requested, self.today)

    def test_daily_publication_selects_existing_file_date_not_missing_yesterday(self):
        self.assertEqual(self.select(['csv/2026-10-14.csv']), {'monitor': 'true', 'date': '2026-10-14'})

    def test_late_publication_still_uses_actual_csv_date_after_midnight(self):
        (self.root / 'csv/2026-10-15.csv').write_text('generated csv')
        result = select_input(self.root, ['csv/2026-10-15.csv'], 'push', today=date(2026, 10, 18))
        self.assertEqual(result['date'], '2026-10-15')

    def test_code_or_summary_push_does_not_monitor(self):
        for path in ['scripts/notify_dingtalk.py', '.github/workflows/channel-monitor.yml',
                     'csv/weekly/2026-W41.csv', 'quality/channel_monitor/latest.csv']:
            self.assertEqual(self.select([path])['monitor'], 'false')

    def test_future_unfinished_deleted_files_do_not_trigger(self):
        (self.root / 'csv/2026-10-16.csv').write_text('in progress')
        for path in ['csv/2026-10-16.csv', 'csv/2026-10-15.csv', 'csv/not-a-date.csv']:
            self.assertEqual(self.select([path])['monitor'], 'false')

    def test_no_clock_schedule_trigger(self):
        self.assertEqual(self.select(['csv/2026-10-14.csv'], event='schedule')['monitor'], 'false')

    def test_manual_requires_present_completed_csv(self):
        self.assertEqual(self.select([], event='workflow_dispatch')['date'], '2026-10-14')
        with self.assertRaises(ValueError):
            self.select([], event='workflow_dispatch', requested='2026-10-15')

    def test_backfill_refreshes_latest_completed_day(self):
        (self.root / 'csv/2026-10-10.csv').write_text('backfilled')
        self.assertEqual(self.select(['csv/2026-10-10.csv'])['date'], '2026-10-14')

    def test_git_diff_only_returns_actual_added_and_modified_files(self):
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=self.root, text=True).strip()
        git('init', '-q')
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.com')
        git('add', '.')
        git('commit', '-qm', 'initial')
        before = git('rev-parse', 'HEAD')
        (self.root / 'csv/2026-10-15.csv').write_text('new daily csv')
        (self.root / 'README.md').write_text('maintenance')
        git('add', '.')
        git('commit', '-qm', 'publish')
        after = git('rev-parse', 'HEAD')
        self.assertEqual(set(changed_files(self.root, before, after)), {'csv/2026-10-15.csv', 'README.md'})


if __name__ == '__main__':
    unittest.main()
