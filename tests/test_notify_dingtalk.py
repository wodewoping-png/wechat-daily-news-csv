import base64
import csv
import hashlib
import hmac
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from scripts.notify_dingtalk import (
    BODY_BUDGET, DeliveryError, build_messages, deliver, main, post_json,
    read_snapshot, send_part, signed_url, validated_webhook,
)

WEBHOOK = 'https://oapi.dingtalk.com/robot/send?access_token=TEST_ONLY_TOKEN'


def fixture(count=238):
    channels = [dict(date='2026-10-14', channel_id=f'wx:TEST_{i}', account_name=f'公众号渠道{i:03d}',
                     observed_articles=str(i % 7), status_label='正常：已抓到文章' if i % 7 else '当天未抓到文章',
                     severity='info') for i in range(count)]
    report = dict(as_of='2026-10-14', channel_count=count, observed_active_channels=100,
                  observed_articles=601, feed_status='ok', alerts=[])
    return report, channels


class DingTalkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state.json'

    def test_signature_matches_official_algorithm(self):
        secret, timestamp = 'SEC_TEST_ONLY', 1700000000000
        url = signed_url(WEBHOOK, secret, timestamp)
        query = parse_qs(urlsplit(url).query)
        expected = base64.b64encode(hmac.new(secret.encode(), f'{timestamp}\n{secret}'.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(query['sign'], [expected])
        self.assertEqual(query['timestamp'], [str(timestamp)])
        self.assertEqual(query['access_token'], ['TEST_ONLY_TOKEN'])

    def test_invalid_and_redirect_webhooks_rejected_without_secret_leak(self):
        for url in ['http://oapi.dingtalk.com/robot/send?access_token=SECRET',
                    'https://oapi.dingtalk.com.attacker.test/robot/send?access_token=SECRET',
                    'https://user:SECRET@oapi.dingtalk.com/robot/send?access_token=SECRET',
                    'https://oapi.dingtalk.com/robot/send']:
            with self.assertRaises(DeliveryError) as ctx:
                validated_webhook(url)
            self.assertNotIn('SECRET', str(ctx.exception))

    def test_all_channels_zero_and_unknown_preserved_in_bounded_parts(self):
        report, channels = fixture()
        channels[-1]['observed_articles'] = ''
        channels[-1]['status_label'] = '数据不完整，无法判断'
        messages = build_messages(report, channels, [], 'owner/repo')
        all_text = '\n'.join(m['markdown']['text'] for m in messages)
        self.assertGreater(len(messages), 1)
        for c in channels:
            self.assertEqual(all_text.count(c['account_name'] + '：'), 1)
        self.assertIn('公众号渠道237：未知', all_text)
        self.assertIn('公众号渠道000：0篇', all_text)
        for m in messages:
            self.assertIn('wechat', m['markdown']['text'])
            self.assertFalse(m['at']['isAtAll'])
            self.assertLessEqual(len(json.dumps({**m, 'msgUuid': 'x' * 64}, ensure_ascii=False).encode()), BODY_BUDGET)

    def test_anomalies_and_recovery_included(self):
        report, channels = fixture(1)
        report['alerts'] = [dict(account_name='渠道异常', severity='warning', detail='三天无文章', suggested_action='核查')]
        recovered = [dict(account_name='恢复渠道', rule='volume_drop')]
        text = ''.join(m['markdown']['text'] for m in build_messages(report, channels, recovered, 'owner/repo'))
        self.assertIn('渠道异常', text)
        self.assertIn('恢复渠道', text)
        self.assertIn('建议：核查', text)

    def test_long_line_fails_instead_of_dropping_channels(self):
        report, channels = fixture(1)
        channels[0]['account_name'] = '过长名称' * 5000
        with self.assertRaises(DeliveryError):
            build_messages(report, channels, [], 'owner/repo')

    def test_duplicate_skip_and_destination_change(self):
        report, channels = fixture(1)
        messages = build_messages(report, channels, [], 'owner/repo')
        calls = []
        def post(url, payload):
            calls.append(payload)
            return {'errcode': '0'}
        self.assertEqual(deliver(messages, WEBHOOK, '', self.state, report['as_of'], post=post, sleep=lambda _: None), (1, 0))
        self.assertEqual(deliver(messages, WEBHOOK, '', self.state, report['as_of'], post=post, sleep=lambda _: None), (0, 1))
        self.assertEqual(len(calls), 1)
        deliver(messages, WEBHOOK + '2', '', self.state, report['as_of'], post=post, sleep=lambda _: None)
        self.assertEqual(len(calls), 2)
        self.assertNotIn('TEST_ONLY_TOKEN', self.state.read_text())

    def test_partial_success_is_saved_and_retry_only_sends_remaining(self):
        report, channels = fixture()
        messages = build_messages(report, channels, [], 'owner/repo')
        calls = []
        def post(url, payload):
            calls.append(payload['msgUuid'])
            return {'errcode': 0 if len(calls) == 1 else 310000, 'errmsg': 'SECRET'}
        with self.assertRaises(DeliveryError):
            deliver(messages, WEBHOOK, '', self.state, report['as_of'], post=post, sleep=lambda _: None)
        self.assertEqual(len(json.loads(self.state.read_text())['delivered']), 1)
        result = deliver(messages, WEBHOOK, '', self.state, report['as_of'], post=lambda *_: {'errcode': 0}, sleep=lambda _: None)
        self.assertEqual(result, (len(messages) - 1, 1))

    def test_timeout_retry_uses_same_server_idempotency_key(self):
        calls = []
        def post(url, payload):
            calls.append(payload['msgUuid'])
            if len(calls) == 1:
                raise DeliveryError('网络超时', True)
            return {'errcode': 0}
        send_part(WEBHOOK, '', {'msgtype': 'markdown'}, 'stable-id', post=post, sleep=lambda _: None)
        self.assertEqual(calls, ['stable-id', 'stable-id'])

    def test_application_error_not_recorded_and_raw_message_not_logged(self):
        report, channels = fixture(1)
        with self.assertRaises(DeliveryError) as ctx:
            deliver(build_messages(report, channels, [], 'owner/repo'), WEBHOOK, '', self.state, report['as_of'],
                    post=lambda *_: {'errcode': 310000, 'errmsg': 'TOKEN=SECRET'}, sleep=lambda _: None)
        self.assertNotIn('SECRET', str(ctx.exception))
        self.assertFalse(self.state.exists())

    def test_transport_errors_sanitize_url(self):
        for error in [URLError('TOKEN=SECRET'), HTTPError(WEBHOOK, 503, 'SECRET', {}, None)]:
            with patch('scripts.notify_dingtalk.build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(DeliveryError) as ctx:
                    post_json(WEBHOOK, {})
                self.assertNotIn('SECRET', str(ctx.exception))
                self.assertNotIn('TEST_ONLY_TOKEN', str(ctx.exception))

    def test_snapshot_mismatch_blocks_send(self):
        folder = self.root / 'quality/channel_monitor'
        folder.mkdir(parents=True)
        report, channels = fixture(1)
        (folder / 'latest_alerts.json').write_text(json.dumps(report))
        channels[0]['date'] = '2026-10-13'
        with (folder / 'latest.csv').open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(channels[0])); w.writeheader(); w.writerows(channels)
        with self.assertRaises(DeliveryError):
            read_snapshot(self.root)

    def test_no_webhook_is_explicit_skip(self):
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['--repo-root', str(self.root)]), 0)
        self.assertIn('未发送', out.getvalue())


if __name__ == '__main__':
    unittest.main()
