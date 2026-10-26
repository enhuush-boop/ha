import unittest
from unittest.mock import patch
from app.services.text_analysis import analyze_text, _chunks

class ChecksTests(unittest.TestCase):
    def test_checks_cover_unavailable_and_empty(self):
        for text in ["", "12345", "aaaaaaaaaaa"]:
            with self.subTest(text=text), patch("app.services.text_analysis.get_text_model") as loader:
                result=analyze_text(text)
                self.assertEqual(len(result["checks"]),18)
                self.assertEqual(next(x for x in result["checks"] if x["id"]=="plagiarism")["status"],"unavailable")
                loader.assert_not_called()
    def test_sentence_duplicates_and_invisible(self):
        with patch("app.services.text_analysis.get_text_model", return_value=(None, "offline")):
            result=analyze_text("Монгол хэл. Монгол хэл. \u200b")
        checks={c["id"]:c for c in result["checks"]}
        self.assertEqual(checks["sentences"]["value"],1)
        self.assertEqual(checks["characters"]["value"],1)
    def test_long_paragraph_windows_are_bounded(self):
        chunks=_chunks("word "*2300)
        self.assertEqual(len(chunks),8)
        self.assertTrue(all(len(c.split())==250 for c in chunks))

if __name__=="__main__": unittest.main()
