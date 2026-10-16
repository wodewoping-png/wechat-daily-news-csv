import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.generate_periodic_summary import load_articles, write_rollup_csv
from scripts.normalize_article_csv import normalize_csv


class ArticleCSVFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'csv').mkdir()
        self.path = self.root / 'csv/2026-10-15.csv'

    def write(self, rows):
        with self.path.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['title', 'account_name', 'publish_time', 'url', 'digest', 'clean_text', 'extra_field'])
            w.writeheader(); w.writerows(rows)

    def read(self, path=None):
        with (path or self.path).open(encoding='utf-8-sig', newline='') as f:
            r = csv.DictReader(f)
            return r.fieldnames, list(r)

    def test_removes_only_digest_and_preserves_long_body_and_other_columns(self):
        body = '正文内容' * 40000
        self.write([{'title': '原文章', 'digest': '独立摘要', 'clean_text': body, 'extra_field': '保留'}])
        self.assertTrue(normalize_csv(self.path))
        fields, rows = self.read()
        self.assertNotIn('digest', fields)
        self.assertEqual(rows[0]['clean_text'], body)
        self.assertEqual(rows[0]['extra_field'], '保留')
        self.assertFalse(normalize_csv(self.path))

    def test_legacy_digest_only_is_retained_as_fallback(self):
        self.write([{'digest': '仅存内容', 'clean_text': ''}])
        normalize_csv(self.path)
        self.assertEqual(self.read()[1][0]['clean_text'], '仅存内容')

    def test_exact_excluded_fakeid_does_not_drop_correct_same_name(self):
        self.write([{'account_name': '创新研究', 'url': 'https://mp.weixin.qq.com/s?__biz=BAD==&mid=1', 'clean_text': '误抓取'},
                    {'account_name': '创新研究', 'url': 'https://mp.weixin.qq.com/s?__biz=GOOD==&mid=2', 'clean_text': '保留'}])
        normalize_csv(self.path, {'BAD=='})
        self.assertEqual([r['clean_text'] for r in self.read()[1]], ['保留'])

    def test_periodic_csv_accepts_old_digest_but_no_longer_exports_column(self):
        self.write([{'title': '老数据', 'account_name': '渠道', 'digest': '仅摘要', 'publish_time': '2026-10-15T08:00:00+08:00', 'url': 'https://example.com/1'}])
        day = date(2026, 10, 15)
        articles, missing = load_articles(self.root, day, day)
        out = self.root / 'weekly.csv'
        write_rollup_csv(articles, out)
        self.assertEqual(missing, [])
        self.assertNotIn('digest', self.read(out)[0])
        self.assertEqual(self.read(out)[1][0]['clean_text'], '仅摘要')


if __name__ == '__main__':
    unittest.main()
