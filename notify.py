"""生成完了時に共有URLをスマホへ通知する（Discord / Slack Incoming Webhook）。

Webhook URL は秘密のため config.json の `notify_webhook` にのみ置く（リポジトリには出さない）。
日本語本文を安全に扱うため .bat ではなくこの Python から送信する。
"""

from __future__ import annotations

import argparse
import json
import urllib.request

from collect_trends import load_config


def webhook_payload_key(webhook: str) -> str:
    """Slack は text、Discord など他は content を使う。"""
    return "text" if "slack.com" in webhook else "content"


def build_message(result_date: str, next_date: str, share_url: str) -> str:
    parts = ["🏇 傾向レポートを更新しました"]
    if result_date:
        detail = f"{result_date} の傾向"
        if next_date:
            detail += f" / {next_date} のおすすめ"
        parts.append(detail)
    if share_url:
        parts.append(share_url)
    return "\n".join(parts)


def send_webhook(webhook: str, message: str, *, timeout: int = 10) -> int:
    key = webhook_payload_key(webhook)
    data = json.dumps({key: message}).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        # Discord は Cloudflare 経由で Python 既定のUAを403で弾くため、明示的にUAを付ける。
        headers={"Content-Type": "application/json", "User-Agent": "zenjitsu-keiko-notifier/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return getattr(resp, "status", 0) or 0


def main() -> int:
    ap = argparse.ArgumentParser(description="生成完了通知をWebhookへ送信します")
    ap.add_argument("--result-date", default="")
    ap.add_argument("--next-date", default="")
    args = ap.parse_args()

    config = load_config()
    webhook = str(config.get("notify_webhook") or "").strip()
    if not webhook:
        print("通知スキップ: config.json の notify_webhook が未設定です。")
        return 0

    message = build_message(args.result_date, args.next_date, str(config.get("share_url") or ""))
    try:
        status = send_webhook(webhook, message)
        print(f"通知送信: HTTP {status}")
    except Exception as exc:  # 通知失敗はバッチ全体を止めない
        print(f"通知失敗: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
