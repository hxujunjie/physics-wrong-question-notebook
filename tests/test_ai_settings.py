import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import ai_settings


class AiSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_list_presets_includes_domestic_and_global_providers(self):
        presets = {item["id"]: item for item in ai_settings.list_presets()}
        for pid in ("dashscope", "volcengine", "zhipu", "openai", "google", "xai", "custom"):
            self.assertIn(pid, presets)
        self.assertTrue(presets["zhipu"]["models"])
        self.assertTrue(any(m["id"] == "gpt-4o-mini" for m in presets["openai"]["models"]))
        self.assertTrue(any(m["id"] == "gemini-3.5-flash" for m in presets["google"]["models"]))
        self.assertTrue(any(m["id"] == "grok-2-vision-1212" for m in presets["xai"]["models"]))

    def test_save_and_summary_roundtrip(self):
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            saved = ai_settings.save("test-key-12345678", model="qwen2.5-vl-7b-instruct", provider="dashscope", path=self.path)
            self.assertTrue(saved["configured"])
            self.assertEqual(saved["provider"], "dashscope")
            self.assertEqual(saved["model"], "qwen2.5-vl-7b-instruct")
            self.assertIn("dashscope.aliyuncs.com", saved["effective_base_url"])
            conn = ai_settings.get_connection(self.path)
            self.assertEqual(conn["api_key"], "test-key-12345678")
            self.assertEqual(conn["provider"], "dashscope")

    def test_auto_detect_sk_key_defaults_to_dashscope_free_model(self):
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            saved = ai_settings.save("sk-testkey-12345678", path=self.path, auto_detect=True)
            self.assertEqual(saved["provider"], "dashscope")
            self.assertEqual(saved["model"], "qwen2.5-vl-3b-instruct")
            self.assertNotIn(saved["model"], {"qwen-vl-plus", "qwen-vl-max"})
            self.assertTrue(saved.get("auto_detected"))

    def test_auto_detect_zhipu_dot_key_not_dashscope(self):
        """智谱密钥 id.secret 含点号，绝不能落到通义千问。"""
        zhipu_key = "abcdef12ghijkl34.mnopqrstuvwx9012345678yz"
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            saved = ai_settings.save(zhipu_key, path=self.path, auto_detect=True)
            self.assertEqual(saved["provider"], "zhipu")
            self.assertEqual(saved["model"], "glm-4.6v-flash")
            self.assertIn("bigmodel.cn", saved["effective_base_url"])
            self.assertNotIn("dashscope", saved["effective_base_url"])

    def test_defaults_use_free_not_paid_flagship_models(self):
        self.assertEqual(ai_settings.PROVIDER_PRESETS["zhipu"]["default_model"], "glm-4.6v-flash")
        self.assertEqual(ai_settings.PROVIDER_PRESETS["dashscope"]["default_model"], "qwen2.5-vl-3b-instruct")
        self.assertNotEqual(ai_settings.PROVIDER_PRESETS["zhipu"]["default_model"], "glm-4v")
        self.assertNotEqual(ai_settings.PROVIDER_PRESETS["dashscope"]["default_model"], "qwen-vl-plus")

    def test_auto_detect_replaces_legacy_grok_model(self):
        self.path.write_text(
            json.dumps({"provider": "custom", "api_key": "enc:old", "model": "grok-4.5", "base_url": "https://api.x.ai/v1"}),
            encoding="utf-8",
        )
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            saved = ai_settings.save("sk-newkey-12345678", path=self.path, auto_detect=True)
            self.assertEqual(saved["provider"], "dashscope")
            self.assertNotEqual(saved["model"], "grok-4.5")
            self.assertEqual(saved["model"], "qwen2.5-vl-3b-instruct")
            self.assertIn("dashscope.aliyuncs.com", saved["effective_base_url"])
            self.assertNotIn("api.x.ai", saved["effective_base_url"])

    def test_auto_detect_ignores_stale_form_grok_fields(self):
        """UI may still show old grok-4.5 + xAI; auto_detect must not keep them."""
        self.path.write_text(
            json.dumps({"provider": "custom", "api_key": "enc:old", "model": "grok-4.5", "base_url": "https://api.x.ai/v1"}),
            encoding="utf-8",
        )
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            saved = ai_settings.save(
                "sk-newkey-12345678",
                model="grok-4.5",
                path=self.path,
                provider="custom",
                base_url="https://api.x.ai/v1",
                auto_detect=True,
            )
            self.assertEqual(saved["provider"], "dashscope")
            self.assertEqual(saved["model"], "qwen2.5-vl-3b-instruct")
            self.assertIn("dashscope.aliyuncs.com", saved["effective_base_url"])

    def test_new_sk_key_migrates_legacy_even_without_auto_detect_flag(self):
        self.path.write_text(
            json.dumps({"provider": "custom", "api_key": "enc:old", "model": "grok-4.5", "base_url": "https://api.x.ai/v1"}),
            encoding="utf-8",
        )
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            saved = ai_settings.save("sk-newkey-12345678", path=self.path, auto_detect=False)
            self.assertEqual(saved["provider"], "dashscope")
            self.assertEqual(saved["model"], "qwen2.5-vl-3b-instruct")

    def test_xai_key_still_selects_xai_endpoint(self):
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            saved = ai_settings.save("xai-testkey-12345678", path=self.path, auto_detect=True)
            self.assertEqual(saved["provider"], "xai")
            self.assertIn("api.x.ai", saved["effective_base_url"])
            self.assertTrue(str(saved["model"]).startswith("grok"))

    def test_google_and_openai_key_shapes(self):
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            g = ai_settings.save("AIzaSyDummyGoogleKey1234567890", path=self.path, auto_detect=True)
            self.assertEqual(g["provider"], "google")
            self.assertEqual(g["model"], "gemini-3.5-flash")
            o = ai_settings.save("sk-proj-OpenAITestKey1234567890", path=self.path, auto_detect=True)
            self.assertEqual(o["provider"], "openai")
            self.assertEqual(o["model"], "gpt-4o-mini")

    def test_update_model_without_reentering_key(self):
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"), patch.object(
            ai_settings, "_unprotect_any", side_effect=lambda value: value.split(":", 1)[1]
        ):
            ai_settings.save("sk-testkey-12345678", path=self.path, auto_detect=True)
            saved = ai_settings.save("", model="qwen2.5-vl-7b-instruct", provider="dashscope", path=self.path, auto_detect=False)
            self.assertEqual(saved["model"], "qwen2.5-vl-7b-instruct")
            self.assertEqual(ai_settings.get_connection(self.path)["model"], "qwen2.5-vl-7b-instruct")

    def test_custom_requires_base_url(self):
        with patch.object(ai_settings, "_protect", side_effect=lambda value, **_kwargs: f"enc:{value}"):
            with self.assertRaisesRegex(ValueError, "接口地址"):
                ai_settings.save("test-key-12345678", model="my-model", provider="custom", path=self.path)

    def test_legacy_grok_settings_migrate(self):
        self.path.write_text(
            '{"grok_api_key":"enc:legacy-key-abcdefgh","model":"grok-4.5"}',
            encoding="utf-8",
        )
        with patch.object(ai_settings, "_unprotect_any", return_value="legacy-key-abcdefgh"):
            summary = ai_settings.summary(self.path)
        self.assertTrue(summary["configured"])
        self.assertEqual(summary["provider"], "xai")
        self.assertIn("api.x.ai", summary["effective_base_url"])


if __name__ == "__main__":
    unittest.main()
