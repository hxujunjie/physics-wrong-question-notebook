import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import ai_client


class AiClientParseTests(unittest.TestCase):
    def test_parse_json_with_markdown_fence_and_chinese(self):
        raw = '说明如下：\n```json\n{"ok": true, "note": "连接成功"}\n```'
        data = ai_client._parse_json_object(raw)
        self.assertTrue(data["ok"])
        self.assertEqual(data["note"], "连接成功")

    def test_parse_json_with_trailing_extra_data(self):
        """Models often append a second object or Chinese notes after valid JSON."""
        raw = (
            '{\n  "pages": [\n    {"page_number": 1, "anchor_text": "第一章", "questions": []}\n  ]\n}\n'
            '说明：以上为识别结果\n'
            '{"pages": []}\n'
        )
        data = ai_client._parse_json_object(raw)
        self.assertEqual(data["pages"][0]["page_number"], 1)
        self.assertEqual(data["pages"][0]["anchor_text"], "第一章")

    def test_extract_prefers_content_then_reasoning(self):
        message = SimpleNamespace(content=None, reasoning_content='{"ok": true}')
        self.assertEqual(ai_client._extract_message_text(message), '{"ok": true}')
        message2 = SimpleNamespace(content='{"ok": false}', reasoning_content='{"ok": true}')
        self.assertEqual(ai_client._extract_message_text(message2), '{"ok": false}')

    def test_unicode_encode_error_not_mislabeled_as_json(self):
        client = ai_client.AiClient("test-key-12345678", "glm-4.6v-flash", "https://open.bigmodel.cn/api/paas/v4", provider="zhipu")

        def boom(**_kwargs):
            "测试中文内容是否被误报".encode("ascii")

        with patch.object(client, "_create_completion", side_effect=boom):
            with self.assertRaises(ai_client.AiError) as ctx:
                client.test_connection()
        self.assertNotIn("JSON 无法解析", str(ctx.exception))
        self.assertIn("编码", str(ctx.exception))

    def test_zhipu_disables_thinking_in_extra_body(self):
        client = ai_client.AiClient("test-key-12345678", "glm-4.6v-flash", "https://open.bigmodel.cn/api/paas/v4", provider="zhipu")
        self.assertEqual(client._provider_request_extras().get("thinking", {}).get("type"), "disabled")

        mock_response = MagicMock()
        mock_response.choices = [SimpleNamespace(message=SimpleNamespace(content='{"ok": true}', reasoning_content=None))]
        mock_response.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        with patch.object(client, "_create_completion", return_value=mock_response) as create:
            usage = client.test_connection()
        self.assertEqual(usage["total_tokens"], 2)
        # Called with use_json_object True first
        self.assertTrue(create.called)

    def test_rate_limit_message_is_actionable(self):
        from openai import APIStatusError

        client = ai_client.AiClient("test-key-12345678", "glm-4.6v-flash", "https://open.bigmodel.cn/api/paas/v4", provider="zhipu")
        response = MagicMock()
        response.status_code = 429
        response.headers = {}
        response.text = "rate"
        error = APIStatusError(
            message="Error code: 429 - rate limit",
            response=response,
            body={"error": {"code": "1302", "message": "您的账户已达到速率限制"}},
        )
        with patch.object(client, "_create_completion", side_effect=error), patch.object(ai_client.time, "sleep"):
            with self.assertRaises(ai_client.RateLimitError) as ctx:
                client.test_connection()
        self.assertIn("速率限制", str(ctx.exception))
        self.assertIn("分钟", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
