import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.monitor_channels import run, url_identity
from scripts.notify_channel_monitor import sync_issue, MARKER


class ChannelMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'channels').mkdir()
        (self.root / 'csv').mkdir()
        (self.root / 'channels/monitor_config.json').write_text('{}')
        with (self.root / 'channels/seed_accounts.csv').open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['account_name', 'fakeid', 'wechat_alias', 'listed_since', 'source_reference'])
            w.writerow(['原名称', 'AAA==', 'wx_alias', '2026-08-18', 'test seed'])

    def daily(self, day, articles=None):
        if articles is None:
            articles = [('原名称', 'AAA==', str(day), 3)]
        with (self.root / 'csv' / f'{day}.csv').open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['title', 'account_name', 'source', 'url', 'publish_time'])
            w.writeheader()
            for name, biz, mid, count in articles:
                for i in range(count):
                    w.writerow({'title': f'文章{i}', 'account_name': name, 'source': name,
                                'url': f'https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={i+1}&scene=1',
                                'publish_time': f'{day}T08:00:00+08:00'})

    def execute(self, day):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(self.root, day)

    def latest(self):
        with (self.root / 'quality/channel_monitor/latest.csv').open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))

    def test_fakeid_rename_and_tracking_query_dedup(self):
        d = date(2026, 10, 1)
        self.daily(d, [('新名称', 'AAA==', '123', 1), ('新名称', 'AAA==', '123', 1)])
        result = self.execute(d)
        self.assertEqual(result['channel_count'], 1)
        self.assertEqual(self.latest()[0]['observed_articles'], '1')
        registry = json.loads((self.root / 'channels/registry.json').read_text())['channels'][0]
        self.assertEqual(registry['wechat_alias'], 'wx_alias')
        self.assertEqual(registry['seed_name'], '原名称')
        self.assertEqual(registry['account_name'], '新名称')
        a, _ = url_identity('http://mp.weixin.qq.com/s?__biz=AAA==&mid=1&idx=1&scene=1')
        b, _ = url_identity('https://mp.weixin.qq.com/s?idx=1&mid=1&__biz=AAA==&scene=2')
        self.assertEqual(a, b)

    def test_new_channel_added_and_same_name_other_fakeid_not_merged(self):
        d = date(2026, 10, 1)
        self.daily(d, [('原名称', 'BBB==', '123', 1), ('新信息源', 'CCC==', '124', 1)])
        result = self.execute(d)
        self.assertEqual(result['channel_count'], 3)
        self.assertTrue(any(a['rule'] == 'identity_conflict' for a in result['alerts']))

    def test_no_future_date_information_leaks(self):
        d = date(2026, 10, 1)
        self.daily(d)
        self.daily(d + timedelta(days=1), [('未来新增', 'BBB==', '123', 1)])
        result = self.execute(d)
        self.assertEqual(result['channel_count'], 1)

    def test_missing_day_is_unknown_not_zero_or_channel_failure(self):
        d = date(2026, 10, 1)
        self.daily(d)
        result = self.execute(d + timedelta(days=1))
        row = self.latest()[0]
        self.assertEqual(row['status'], 'unknown')
        self.assertEqual(row['observed_articles'], '')
        self.assertEqual([a['rule'] for a in result['alerts']], ['daily_feed_unavailable'])

    def test_empty_file_is_unavailable(self):
        d = date(2026, 10, 1)
        self.daily(d, [])
        result = self.execute(d)
        self.assertEqual(result['feed_status'], 'empty')
        self.assertEqual(self.latest()[0]['status'], 'unknown')

    def test_one_zero_day_not_confirmed_failure(self):
        d = date(2026, 10, 1)
        self.daily(d)
        self.daily(d + timedelta(days=1), [('别的渠道', 'BBB==', '123', 1)])
        result = self.execute(d + timedelta(days=1))
        seed = next(r for r in self.latest() if r['channel_id'] == 'wx:AAA==')
        self.assertEqual(seed['status'], 'zero_observed')
        self.assertFalse(any(a['rule'] == 'fetch_error' for a in result['alerts']))

    def test_silence_baseline_and_recovery_and_idempotency(self):
        start = date(2026, 9, 1)
        for i in range(18):
            self.daily(start + timedelta(days=i))
        for i in range(18, 21):
            self.daily(start + timedelta(days=i), [('另一个渠道', 'BBB==', str(i), 1)])
        target = start + timedelta(days=20)
        result = self.execute(target)
        self.assertTrue(any(a['rule'] == 'suspected_silence' for a in result['alerts']))
        before = (self.root / 'quality/channel_monitor/incident_events.csv').read_bytes()
        self.execute(target)
        self.assertEqual(before, (self.root / 'quality/channel_monitor/incident_events.csv').read_bytes())
        self.daily(target + timedelta(days=1))
        self.execute(target + timedelta(days=1))
        state = json.loads((self.root / 'quality/channel_monitor/state.json').read_text())
        self.assertTrue(any(e['event'] == 'recovered' and e['rule'] == 'suspected_silence' for e in state['events']))

    def test_missing_day_does_not_increment_silence_or_fake_recovery(self):
        start = date(2026, 9, 1)
        for i in range(18):
            self.daily(start + timedelta(days=i))
        for i in range(18, 21):
            self.daily(start + timedelta(days=i), [('其他', 'BBB==', str(i), 1)])
        target = start + timedelta(days=20)
        self.execute(target)
        result = self.execute(target + timedelta(days=1))
        self.assertTrue(any(a['rule'] == 'suspected_silence' for a in result['alerts']))
        self.assertEqual(self.latest()[0]['consecutive_observed_zero_days'], '0')

    def test_telemetry_final_attempt_and_secret_exclusion(self):
        d = date(2026, 10, 1)
        self.daily(d, [('其他', 'BBB==', '123', 1)])
        p = self.root / 'quality/channel_fetch_events'
        p.mkdir(parents=True)
        event_file = p / f'{d}.jsonl'
        event_file.write_text(json.dumps({'fakeid': 'AAA==', 'status': 'error', 'error_code': 'HTTP_401', 'message': 'token=SECRET'}) + '\n')
        result = self.execute(d)
        self.assertTrue(any(a['rule'] == 'fetch_error' for a in result['alerts']))
        self.assertNotIn('SECRET', (self.root / 'quality/channel_monitor/latest.md').read_text())
        with event_file.open('a') as f:
            f.write(json.dumps({'fakeid': 'AAA==', 'status': 'success'}) + '\n')
        result = self.execute(d)
        self.assertFalse(any(a['rule'] == 'fetch_error' for a in result['alerts']))

    def test_missing_telemetry_does_not_clear_confirmed_error(self):
        d = date(2026, 10, 1)
        self.daily(d, [('其他', 'BBB==', '123', 1)])
        p = self.root / 'quality/channel_fetch_events'
        p.mkdir(parents=True)
        (p / f'{d}.jsonl').write_text(json.dumps({'fakeid': 'AAA==', 'status': 'error', 'error_code': 'HTTP_401'}) + '\n')
        self.execute(d)
        self.daily(d + timedelta(days=1), [('其他', 'BBB==', '124', 1)])
        result = self.execute(d + timedelta(days=1))
        self.assertTrue(any(a['rule'] == 'fetch_error' for a in result['alerts']))

    def test_backfill_and_revised_daily_inputs_recalculate(self):
        d = date(2026, 10, 1)
        self.daily(d)
        self.execute(d)
        self.daily(d, [('原名称', 'AAA==', '123', 5)])
        result = self.execute(d)
        self.assertEqual(result['observed_articles'], 5)

    def test_managed_issue_create_update_dedup_and_close(self):
        calls = []
        issue = {'number': 9, 'body': MARKER + ' old'}
        def api(method, path, body):
            calls.append((method, path, body))
            return [issue] if method == 'GET' else {}
        payload = {'as_of': '2026-10-01', 'channel_count': 1, 'feed_status': 'ok',
                   'alerts': [{'severity': 'warning', 'account_name': '名称', 'detail': '待核查', 'suggested_action': '检查'}]}
        sync_issue(payload, 'owner/repo', api)
        self.assertEqual(calls[-1][0], 'PATCH')
        issue['body'] = calls[-1][2]['body']
        calls.clear()
        sync_issue(payload, 'owner/repo', api)
        self.assertEqual(len(calls), 1)
        payload['alerts'] = []
        sync_issue(payload, 'owner/repo', api)
        self.assertEqual(calls[-1][2]['state'], 'closed')


if __name__ == '__main__':
    unittest.main()
