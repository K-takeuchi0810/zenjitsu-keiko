import unittest

import notify


class NotifyHelperTests(unittest.TestCase):
    def test_payload_key_is_text_for_slack(self):
        self.assertEqual(
            notify.webhook_payload_key("https://hooks.slack.com/services/XXX"),
            "text",
        )

    def test_payload_key_is_content_for_discord(self):
        self.assertEqual(
            notify.webhook_payload_key("https://discord.com/api/webhooks/123/abc"),
            "content",
        )

    def test_build_message_includes_dates_and_url(self):
        msg = notify.build_message("2026-07-11", "2026-07-12", "https://example.com/report/")
        self.assertIn("2026-07-11 の傾向", msg)
        self.assertIn("2026-07-12 のおすすめ", msg)
        self.assertIn("https://example.com/report/", msg)

    def test_build_message_without_dates_still_has_url(self):
        msg = notify.build_message("", "", "https://example.com/report/")
        self.assertIn("https://example.com/report/", msg)
        self.assertIn("傾向レポート", msg)


if __name__ == "__main__":
    unittest.main()
