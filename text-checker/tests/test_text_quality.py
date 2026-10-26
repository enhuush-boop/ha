import unittest
from unittest.mock import patch
from app.services.text_analysis import analyze_text

class Model:
    model_id = "test-model"
    def predict(self, text):
        return {"ai_probability": 0.95, "latency_ms": 1}

class TextQualityTests(unittest.TestCase):
    def test_short_and_repeated_inputs_skip_model(self):
        for text in ["aaaaaaaaaaaaaaaaaaaaaaaaa", "2026 !!! 12345", "hello " * 100]:
            with self.subTest(text=text), patch("app.services.text_analysis.get_text_model") as loader:
                result = analyze_text(text)
                loader.assert_not_called()
                self.assertEqual(result["label"], "Uncertain")
                self.assertIsNone(result["ai_probability"])
                self.assertEqual(result["quality"]["status"], "insufficient_input")

    def test_short_meaningful_text_gets_model_scores(self):
        with patch("app.services.text_analysis.get_text_model", return_value=(Model(), None)):
            result = analyze_text("Энэ бол богино монгол текст юм.")
        self.assertEqual(result["ai_probability"], 0.95)
        self.assertEqual(result["label"], "Uncertain")
        self.assertTrue(result["quality"]["warnings"])

    def test_mongolian_never_gets_authorship_verdict(self):
        text = "Монгол орны байгаль цаг уурын өөрчлөлтөд хэрхэн дасан зохицож байгааг судлаачид олон жилийн турш ажиглаж байна. Хөдөөгийн иргэд өөрсдийн туршлага дээр үндэслэн ус хөрс ургамлыг хамгаалах шинэ аргуудыг хамтран туршжээ. " * 3
        with patch("app.services.text_analysis.get_text_model", return_value=(Model(), None)):
            result = analyze_text(text)
        self.assertGreater(result["text_stats"]["words"], 50)
        self.assertEqual(result["ai_probability"], 0.95)
        self.assertEqual(result["label"], "Uncertain")
        self.assertEqual(result["quality"]["status"], "unvalidated_language")
        self.assertIsNone(result["confidence"])

    def test_model_failure_does_not_promote_heuristic_to_verdict(self):
        text = " ".join("word" + str(i) for i in range(80)) + " varied writing sample " * 25
        with patch("app.services.text_analysis.get_text_model", return_value=(None, "offline")):
            result = analyze_text(text)
        self.assertEqual(result["label"], "Uncertain")
        self.assertFalse(result["quality"]["classification_available"])

if __name__ == "__main__":
    unittest.main()
