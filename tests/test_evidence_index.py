from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services import evidence_index
from backend.app.services.evidence_index import _parse_feed, search
from backend.app.services.trusted_sources import (
    describe_url,
    source_for_url,
    tier_rank,
    verdict_from_title,
)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Vaccine claim checked &#8211; &#1058;&#1256;&#1256;&#1056;&#1256;&#1043;&#1044;&#1198;&#1198;&#1051;&#1057;&#1069;&#1053;</title>
    <link>https://factcheck.mn/post/1</link>
    <description>&lt;p&gt;A Facebook user said vaccines carry live virus.&lt;/p&gt;</description>
    <pubDate>Tue, 29 Sep 2026 04:26:07 +0000</pubDate>
    <category>Vaccine</category>
  </item>
  <item>
    <title>An ordinary explainer with no verdict</title>
    <link>https://factcheck.mn/post/2</link>
    <description>Background reading.</description>
    <pubDate>Mon, 12 Oct 2026 11:21:53 +0000</pubDate>
  </item>
</channel></rss>"""


class TrustedSourceTests(unittest.TestCase):
    def test_known_domain_and_subdomain_resolve(self):
        self.assertEqual(source_for_url("https://factcheck.mn/post/10980")["id"], "factcheck-mn")
        self.assertEqual(source_for_url("https://www.nso.mn/x")["id"], "nso-mn")

    def test_unlisted_domain_is_not_a_verdict_against_it(self):
        self.assertIsNone(source_for_url("https://some-blog.example/post"))
        described = describe_url("https://some-blog.example/post")
        self.assertFalse(described["listed"])
        self.assertIn("not a verdict", described["note"])

    def test_registry_records_state_ownership_rather_than_hiding_it(self):
        montsame = source_for_url("https://montsame.mn/mn/read/1")
        self.assertEqual(montsame["tier"], "news")
        self.assertIn("pro-government", montsame["note"])

    def test_fact_checker_outranks_news(self):
        self.assertLess(tier_rank({"tier": "fact_check"}), tier_rank({"tier": "news"}))

    def test_verdict_marker_is_read_and_absence_is_not_guessed(self):
        source = source_for_url("https://factcheck.mn/")
        self.assertEqual(
            verdict_from_title(source, "Мэдээлэл ТӨӨРӨГДҮҮЛСЭН"), "Misleading"
        )
        self.assertIsNone(verdict_from_title(source, "Энгийн тайлбар нийтлэл"))


class FeedParsingTests(unittest.TestCase):
    def test_items_are_extracted_and_html_stripped(self):
        items = _parse_feed(RSS)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://factcheck.mn/post/1")
        self.assertNotIn("<p>", items[0]["summary"])
        self.assertIn("live virus", items[0]["summary"])
        self.assertEqual(items[0]["categories"], ["Vaccine"])

    def test_malformed_feed_yields_nothing_rather_than_raising(self):
        self.assertEqual(_parse_feed(b"not xml at all"), [])


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "index.json"
        path.write_text(
            json.dumps(
                {
                    "fetched_at": "2026-10-26T04:00:00+00:00",
                    "items": [
                        {
                            "title": "Вакцинд амьд вирус байдаг тул халдвар тараах эрсдэлтэй гэх мэдээлэл ТӨӨРӨГДҮҮЛСЭН",
                            "url": "https://factcheck.mn/post/10980",
                            "summary": "Фэйсбүүк хэрэглэгч вакцин дотор амьд вирус байна гэж бичжээ.",
                            "published": "Tue, 29 Sep 2026 04:26:07 +0000",
                            "categories": ["Вакцин"],
                            "source_id": "factcheck-mn",
                            "source_name": "FACTCHECK.mn",
                            "tier": "fact_check",
                            "verdict": "Misleading",
                            "key": "",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.patcher = patch.object(evidence_index, "INDEX_PATH", path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_cyrillic_post_finds_the_fact_check(self):
        results = search("Вакцин дотор амьд вирус байгаа гэж байна")["results"]
        self.assertTrue(results)
        self.assertEqual(results[0]["url"], "https://factcheck.mn/post/10980")
        self.assertEqual(results[0]["published_verdict"], "Misleading")

    def test_latin_galig_finds_the_same_fact_check(self):
        results = search("Vaktsind amid virus baidag")["results"]
        self.assertTrue(results)
        self.assertEqual(results[0]["url"], "https://factcheck.mn/post/10980")

    def test_unrelated_post_matches_nothing(self):
        self.assertEqual(search("Өнөөдөр Улаанбаатарт цас орно")["results"], [])

    def test_verdict_is_attributed_to_the_publisher(self):
        result = search("Вакцинд амьд вирус байна")["results"][0]
        self.assertEqual(result["source"], "FACTCHECK.mn")
        self.assertEqual(result["relation"], "fact_check")

    def test_empty_index_says_so_instead_of_pretending(self):
        with tempfile.TemporaryDirectory() as empty:
            with patch.object(evidence_index, "INDEX_PATH", Path(empty) / "none.json"):
                report = search("anything")
                self.assertFalse(report["available"])
                self.assertIn("refresh_evidence", report["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
