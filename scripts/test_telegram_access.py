"""Read-only Telegram access diagnostics; no messages sent."""
import os
import re

import httpx


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    expected_chat_id = os.environ.get("EXPECTED_CHAT_ID", "").strip()
    if expected_chat_id:
        if not re.fullmatch(r"-?\d+", expected_chat_id):
            raise ValueError("Expected Chat ID must be numeric")
        print(f"configured_chat_matches_link={chat_id == expected_chat_id}", flush=True)
        print(f"configured_chat_has_outer_whitespace={chat_id != chat_id.strip()}", flush=True)

    def safe(value):
        text = str(value).replace(token, "[TOKEN]").replace(chat_id, "[CHAT_ID]")
        text = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot[REDACTED]", text)
        return " ".join(text.split())[:500]

    with httpx.Client(timeout=20) as client:
        def request(method, data):
            response = client.post(f"https://api.telegram.org/bot{token}/{method}", data=data)
            body = response.json()
            print(f"{method}: http_status={response.status_code} ok={body.get('ok')}", flush=True)
            if not response.is_success or body.get("ok") is not True:
                print(f"{method}: description={safe(body.get('description', 'ABSENT'))}", flush=True)
                return None
            return body["result"]

        bot = request("getMe", {})
        if bot is None:
            return 1
        chat = request("getChat", {"chat_id": chat_id})
        if chat is None:
            return 1
        print(f"returned_chat_matches_config={str(chat.get('id')) == chat_id}", flush=True)
        if expected_chat_id:
            print(f"returned_chat_matches_link={str(chat.get('id')) == expected_chat_id}", flush=True)
        print(f"chat_type={chat.get('type')} is_forum={chat.get('is_forum', False)}", flush=True)
        member = request("getChatMember", {"chat_id": chat_id, "user_id": bot["id"]})
        if member is None:
            return 1
        print(f"bot_status={member.get('status')} can_send_messages={member.get('can_send_messages', 'not_explicit')}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"TELEGRAM_ACCESS_FAILED type={type(exc).__name__}", flush=True)
        raise SystemExit(1)
