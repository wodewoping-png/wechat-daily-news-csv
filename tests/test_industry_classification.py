import copy
import csv
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.classify_wechat_news import (
    CLASS_FIELDS, MODEL, ModelError, TerminalModelError, batches, load_taxonomy,
    classify_batch, load_cache, merge_weeks, model_article, parse_response, read_json, run, write_json,
)
from scripts.zai_industry_client import ZAIClient

REPO = Path(__file__).resolve().parents[1]


class FakeModel:
    def __init__(self, confidence=0.93, empty=False, fail=False):
        self.calls = []
        self.confidence = confidence
        self.empty = empty
        self.fail = fail

    def generate(self, system, articles):
        self.calls.append(copy.deepcopy(articles))
        if self.fail:
            raise TerminalModelError('未配置 ZAI_API_KEY')
        results = []
        for article in articles:
            matches = [] if self.empty else [
                {'category_id': 'C024', 'confidence': self.confidence, 'reason': '固态电池是本篇核心技术'},
                {'category_id': 'C074', 'confidence': 0.8, 'reason': '正文明确涉及回收技术'}]
            results.append({'article_id': article['article_id'], 'matches': matches, 'reason': '原框架未涵盖的综合事件' if self.empty else '以固态电池为主'})
        return json.dumps({'results': results}, ensure_ascii=False)


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for folder in ['config', 'channels', 'csv']:
            (self.root / folder).mkdir()
        for name in ['industry_taxonomy.json', 'industry_classification.json']:
            (self.root / 'config' / name).write_bytes((REPO / 'config' / name).read_bytes())
        write_json(self.root / 'channels/monitor_config.json', {'excluded_fakeids': ['BAD==']})
        self.today = date(2026, 10, 19)
        self.config = read_json(self.root / 'config/industry_classification.json')
        self.taxonomy, self.allowed = load_taxonomy(self.root)

    def source(self, day='2026-10-15', rows=None):
        fields = ['title', 'account_name', 'publish_time', 'url', 'digest', 'clean_text', 'extra']
        if rows is None:
            rows = [{'title': '固态电池投产', 'account_name': '新闻源', 'publish_time': day+'T12:00:00+08:00',
                     'url': 'https://example.com/1', 'digest': '摘要', 'clean_text': '完整正文', 'extra': '保留字段'}]
        with (self.root / 'csv' / f'{day}.csv').open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)

    def output(self, name='daily/2026-10-15.csv'):
        with (self.root / 'classified' / name).open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            return reader.fieldnames, list(reader)

    def execute(self, model=None, **kwargs):
        return run(self.root, client=model or FakeModel(), today=self.today, **kwargs)

    def test_taxonomy_exactly_125_unique_paths_and_eight_levels(self):
        self.assertEqual(len(self.allowed), 125)
        self.assertEqual(len({tuple(v) for v in self.allowed.values()}), 125)
        self.assertEqual(max(map(len, self.allowed.values())), 8)
        self.assertIn(['零碳产业', '能量转化', '能量存储', '电化学储能', '二次电池', '传统蓄电池', '固态电池'], self.allowed.values())

    def test_full_body_fields_multilabel_and_cache_idempotency(self):
        body = '完整正文' * 40000 + '原文末尾'
        self.source(rows=[{'title': '固态电池', 'url': 'https://example.com/long', 'clean_text': body, 'extra': '保留'}])
        model = FakeModel()
        self.assertEqual(self.execute(model), 0)
        fields, rows = self.output()
        self.assertEqual(rows[0]['clean_text'], body)
        self.assertEqual(model.calls[0][0]['content'], body)
        self.assertNotIn('digest', fields)
        self.assertEqual(rows[0]['extra'], '保留')
        self.assertEqual(rows[0]['第7级'], '固态电池')
        self.assertEqual(rows[0]['第8级'], '')
        self.assertEqual(rows[0]['分类模型'], MODEL)
        self.assertEqual(len(json.loads(rows[0]['其他分类路径'])), 1)
        snapshots = {p.relative_to(self.root): p.read_bytes() for p in (self.root / 'classified').rglob('*') if p.is_file()}
        self.execute(model)
        self.assertEqual(len(model.calls), 1)
        for path, content in snapshots.items():
            self.assertEqual((self.root / path).read_bytes(), content)

    def test_body_revision_reclassifies_and_digest_only_fallback_survives(self):
        self.source(rows=[{'title': '新闻', 'url': 'https://example.com/1', 'digest': '仅摘要'}])
        model = FakeModel()
        self.execute(model)
        self.assertEqual(self.output()[1][0]['clean_text'], '仅摘要')
        self.source(rows=[{'title': '新闻', 'url': 'https://example.com/1', 'clean_text': '更新的正文'}])
        self.execute(model)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(self.output()[1][0]['clean_text'], '更新的正文')

    def test_tracking_url_dedup_exclusion_and_longer_body_preferred(self):
        base = 'https://mp.weixin.qq.com/s?__biz=GOOD==&mid=1&idx=1'
        self.source(rows=[{'title': '新闻', 'url': base+'&scene=1', 'clean_text': '短'},
                          {'title': '新闻', 'url': base+'&scene=2', 'clean_text': '较长正文'},
                          {'title': '误抓取', 'url': 'https://mp.weixin.qq.com/s?__biz=BAD==&mid=2&idx=1'}])
        self.execute()
        self.assertEqual(len(self.output()[1]), 1)
        self.assertEqual(self.output()[1][0]['clean_text'], '较长正文')
        meta = read_json(self.root / 'classified/daily/2026-10-15.json')
        self.assertEqual((meta['source_rows'], meta['excluded_rows'], meta['duplicate_rows']), (3, 1, 1))

    def test_low_confidence_and_outside_framework_are_not_forced_to_category(self):
        self.source()
        self.execute(FakeModel(confidence=0.4))
        row = self.output()[1][0]
        self.assertEqual(row['分类状态'], '待复核')
        self.assertEqual(row['主分类路径'], '')
        self.assertTrue(json.loads(row['其他分类路径']))
        for cache in (self.root / 'classified/cache').glob('*.json'):
            cache.unlink()
        (self.root / 'classified/daily/2026-10-15.json').unlink()
        self.execute(FakeModel(empty=True))
        row = self.output()[1][0]
        self.assertEqual(row['分类状态'], '不在框架内')
        self.assertEqual(row['主分类路径'], '')

    def test_terminal_failure_is_visible_and_resumes_only_failed_rows(self):
        self.source()
        failing = FakeModel(fail=True)
        self.assertEqual(self.execute(failing), 1)
        self.assertEqual(len(failing.calls), 1)
        self.assertEqual(self.output()[1][0]['分类状态'], '分类失败')
        self.assertFalse(read_json(self.root / 'classified/daily/2026-10-15.json')['complete'])
        self.assertEqual(load_cache(self.root), {})
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.output()[1][0]['分类状态'], '已分类')

    def test_partial_checkpoint_reuses_successes_on_retry(self):
        self.config['batch_size'] = 1
        write_json(self.root / 'config/industry_classification.json', self.config)
        self.source(rows=[{'title': '第一篇', 'url': 'https://example.com/1'}, {'title': '第二篇', 'url': 'https://example.com/2'}])
        model = FakeModel()
        original = model.generate
        def generate(system, articles):
            if model.calls:
                raise TerminalModelError('额度不足')
            return original(system, articles)
        model.generate = generate
        self.assertEqual(self.execute(model), 1)
        retry = FakeModel()
        self.assertEqual(self.execute(retry), 0)
        self.assertEqual(len(retry.calls), 1)
        self.assertEqual(retry.calls[0][0]['title'], '第二篇')

    def test_semantic_contract_rejects_missing_extra_duplicate_invalid_results(self):
        good = {'article_id': 'a', 'matches': [{'category_id': 'C001', 'confidence': 0.9, 'reason': '核心事件'}]}
        self.assertIn('a', parse_response('```json\n'+json.dumps({'results': [good]})+'\n```', {'a'}, self.allowed))
        cases = [[], [good, good], [{**good, 'article_id': 'bad'}],
                 [{**good, 'matches': [{'category_id': 'MADE_UP', 'confidence': 1, 'reason': '错误'}]}],
                 [{**good, 'matches': [{'category_id': ['C001'], 'confidence': 1, 'reason': '错误'}]}],
                 [{**good, 'matches': [{'category_id': 'C001', 'confidence': True, 'reason': '错误'}]}]]
        for results in cases:
            with self.subTest(results=results), self.assertRaises(ModelError):
                parse_response(json.dumps({'results': results}), {'a'}, self.allowed)

    def test_legacy_cache_migrates_to_small_hash_buckets(self):
        write_json(self.root / 'classified/cache/results.json', {'aa000': {'matches': []}, 'bb001': {'matches': []}})
        cache = load_cache(self.root)
        self.assertEqual(len(cache), 2)
        self.assertTrue((self.root / 'classified/cache/aa.json').exists())
        self.assertTrue((self.root / 'classified/cache/bb.json').exists())
        self.assertFalse((self.root / 'classified/cache/results.json').exists())

    def test_week_merge_requires_seven_days_deduplicates_and_invalidates_stale(self):
        model = FakeModel()
        start = date(2026, 10, 12)
        for i in range(6):
            self.source(str(start+timedelta(days=i)))
        self.execute(model, start='2026-10-12', end='2026-10-17')
        meta = read_json(self.root / 'classified/weekly/2026-W42.json')
        self.assertFalse(meta['complete'])
        self.assertEqual(meta['missing_dates'], ['2026-10-18'])
        self.source('2026-10-18')
        self.execute(model, start='2026-10-18', end='2026-10-18')
        meta = read_json(self.root / 'classified/weekly/2026-W42.json')
        self.assertTrue(meta['complete'])
        self.assertEqual(meta['articles'], 1)
        self.assertFalse((self.root / 'classified/weekly/2026-W42.partial.csv').exists())
        count_before = len(model.calls)
        self.execute(model, merge_only=True)
        self.assertEqual(len(model.calls), count_before)
        self.source('2026-10-12', [{'title': '修订', 'url': 'https://example.com/new'}])
        merge_weeks(self.root, self.config, self.taxonomy)
        meta = read_json(self.root / 'classified/weekly/2026-W42.json')
        self.assertFalse(meta['complete'])
        self.assertTrue(meta['data_complete'])
        self.assertFalse(meta['classification_complete'])
        self.assertEqual(meta['pending_classification_dates'], ['2026-10-12'])
        self.assertTrue((self.root / 'classified/weekly/2026-W42.csv').exists())
        self.assertEqual(meta['articles'], 2)
        rows = self.output('weekly/2026-W42.csv')[1]
        self.assertEqual(sum(r['分类状态'] == '待分类' for r in rows), 1)

    def test_unclassified_source_day_is_not_omitted_from_week(self):
        for i in range(12, 19):
            self.source(f'2026-10-{i}', [{'title': f'新闻{i}', 'url': f'https://example.com/{i}', 'clean_text': '正文'}])
        self.execute(start='2026-10-15', end='2026-10-15')
        meta = read_json(self.root / 'classified/weekly/2026-W42.json')
        self.assertTrue(meta['data_complete'])
        self.assertFalse(meta['classification_complete'])
        self.assertEqual(meta['articles'], 7)
        self.assertEqual(meta['statuses']['待分类'], 6)
        self.assertEqual(len(self.output('weekly/2026-W42.csv')[1]), 7)

    def test_batch_retains_valid_siblings_and_retries_only_missing_item(self):
        calls = []
        class PartialModel:
            def generate(inner, system, articles):
                calls.append(articles)
                # First batch omits its second article; the second request must
                # contain that article alone, rather than repeat the good one.
                first = articles[:1]
                return json.dumps({'results': [{'article_id': a['article_id'], 'matches': [
                    {'category_id': 'C001', 'confidence': 0.9, 'reason': '中文依据'}]} for a in first]})
        items = [('full-hash-a', 'key-a', {'title': '第一篇', 'content': '正文'}),
                 ('full-hash-b', 'key-b', {'title': '第二篇', 'content': '正文'})]
        result, errors = classify_batch(items, self.taxonomy, self.allowed, PartialModel())
        self.assertEqual(set(result), {'full-hash-a', 'full-hash-b'})
        self.assertEqual(errors, {})
        self.assertEqual([len(c) for c in calls], [2, 1])
        self.assertEqual(calls[0][0]['article_id'], '0')
        self.assertEqual(calls[1][0]['title'], '第二篇')

    def test_explicit_backfill_missing_source_and_future_dates_fail(self):
        with self.assertRaises(FileNotFoundError):
            self.execute(start='2026-10-12', end='2026-10-12')
        with self.assertRaises(ValueError):
            self.execute(end='2026-10-19')

    def test_optional_input_budget_does_not_truncate_default(self):
        body = '开头' + '正文' * 10000 + '结尾'
        self.assertEqual(model_article({'clean_text': body})['content'], body)
        clipped = model_article({'clean_text': body}, 300)['content']
        self.assertTrue(clipped.startswith('开头'))
        self.assertTrue(clipped.endswith('结尾'))
        groups = list(batches([('a', 'k', {'title': '', 'content': body}), ('b', 'k2', {'title': '', 'content': '短'})], 6, 100))
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0][0][2]['content'], body)

    def test_workflow_is_after_publication_no_schedule_and_cache_persisted_on_failure(self):
        text = (REPO / '.github/workflows/industry-classification.yml').read_text()
        self.assertIn('workflows: [periodic-summary]', text)
        self.assertIn('types: [completed]', text)
        self.assertNotIn('schedule:', text)
        self.assertIn('secrets.ZAI_API_KEY', text)
        self.assertIn('git add -A classified', text)
        self.assertIn("always() && steps.classify.outcome != 'skipped'", text)


class ClientTests(unittest.TestCase):
    def test_model_thinking_routes_and_secrets_not_in_error(self):
        requests = []
        def opener(request, timeout):
            requests.append((request, json.loads(request.data)))
            if len(requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 401, 'unauthorized', {}, io.BytesIO(b'sensitive response'))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{"results":[]}'}}]}).encode())
        with patch.dict(os.environ, {'ZAI_API_KEY': 'never-print-this'}, clear=True):
            client = ZAIClient(opener=opener, sleep=lambda _: None)
            self.assertEqual(client.generate('规则', []), '{"results":[]}')
        self.assertIn('api.z.ai/api/coding/paas/v4', requests[0][0].full_url)
        self.assertIn('open.bigmodel.cn/api/paas/v4', requests[1][0].full_url)
        payload = requests[0][1]
        self.assertEqual(payload['model'], 'glm-5.3-flash')
        self.assertEqual(payload['thinking']['type'], 'enabled')
        self.assertEqual(payload['reasoning_effort'], 'max')

    def test_missing_key_and_quota_failure_stop_and_do_not_echo_body(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TerminalModelError):
                ZAIClient().generate('规则', [])
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, 'quota', {}, io.BytesIO(b'{"code":"1113","private":"never-print-this"}'))
        with patch.dict(os.environ, {'ZAI_API_KEY': 'never-print-this'}, clear=True):
            with self.assertRaises(TerminalModelError) as caught:
                ZAIClient(opener=opener, sleep=lambda _: None).generate('规则', [])
            self.assertNotIn('never-print-this', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
