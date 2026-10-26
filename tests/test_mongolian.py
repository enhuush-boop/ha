from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from backend.app.services.factcheck import (
    CORPUS,
    _rating_to_verdict,
    factcheck_claim,
    find_local_match,
)
from backend.app.services.mongolian import detect_language, match_key
from backend.app.services.text_analysis import analyze_text


class MongolianInputTests(unittest.TestCase):
    def test_script_folding_and_informal_galig(self) -> None:
        self.assertEqual(match_key("Дэлхий хавтгай"), match_key("Delhii havtgai"))
        self.assertEqual(detect_language("Дэлхий хавтгай"), "mn_cyrl")
        self.assertEqual(detect_language("Delhii havtgai"), "mn_latn")
        self.assertEqual(
            detect_language("za enehuu repog buten scan hii"), "mn_latn"
        )
        self.assertEqual(detect_language("Vaccines cause autism."), "other")

    def test_both_scripts_find_sourced_claim(self) -> None:
        self.assertGreater(len(CORPUS["entries"]), 0)
        for claim, language in (
            ("Дэлхий хавтгай.", "mn_cyrl"),
            ("Delhii havtgai", "mn_latn"),
            ("Вакцин аутизм үүсгэдэг.", "mn_cyrl"),
            ("Vaksin autizm uusgedeg", "mn_latn"),
        ):
            with self.subTest(claim=claim):
                result = asyncio.run(factcheck_claim(claim))
                self.assertEqual(result["language"], language)
                self.assertEqual(result["verdict"], "False")
                self.assertEqual(result["method"], "curated_corpus")
                self.assertTrue(result["evidence"][0]["url"].startswith("https://"))

    def test_negation_and_unknown_claim_do_not_inherit_false_verdict(self) -> None:
        for claim in (
            "Вакцин аутизм үүсгэдэггүй.",
            "Vaksin autizm uusgedeggui",
            "Vaksin autizm uusgedeggvi",
            "Дэлхий хавтгай биш.",
            "Монголд маргааш цас орно.",
        ):
            with self.subTest(claim=claim):
                entry, _ = find_local_match(claim)
                self.assertIsNone(entry)

    def test_mongolian_publisher_ratings(self) -> None:
        self.assertEqual(_rating_to_verdict("Худал"), "False")
        self.assertEqual(_rating_to_verdict("Үнэн биш"), "False")
        self.assertEqual(_rating_to_verdict("Үнэн"), "True")
        self.assertEqual(_rating_to_verdict("Хагас үнэн"), "Not Sure")

    def test_mongolian_text_counts_words_without_english_model(self) -> None:
        samples = (
            "Энэ бол монгол хэлээр бичсэн өгүүлбэр юм. Дэлхий хавтгай гэсэн мэдээг шалгана.",
            "Ene bol mongol heleer bichsen uguulber yum. Delhii havtgai gesen medee shalgana.",
        )
        with patch(
            "backend.app.services.text_analysis.get_text_model",
            side_effect=AssertionError("English detector must not run on Mongolian"),
        ):
            for sample in samples:
                with self.subTest(sample=sample):
                    result = analyze_text(sample)
                    self.assertTrue(result["language"].startswith("mn_"))
                    self.assertEqual(result["label"], "Uncertain")
                    self.assertIsNone(result["ai_probability"])
                    self.assertGreater(result["text_stats"]["words"], 5)

    def test_manual_galig_selection_handles_ambiguous_spelling(self) -> None:
        sample = "Margaash boroo orno gesen taamag sonsloo."
        self.assertEqual(detect_language(sample), "other")
        with patch(
            "backend.app.services.text_analysis.get_text_model",
            side_effect=AssertionError("English detector must not run"),
        ):
            result = analyze_text(sample, language_hint="mn_latn")
        self.assertEqual(result["language"], "mn_latn")
        self.assertIsNone(result["ai_probability"])


if __name__ == "__main__":
    unittest.main()
