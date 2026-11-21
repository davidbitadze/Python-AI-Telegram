import json
import os
import urllib.error
import urllib.request


def send_telegram_message(text: str, parse_mode: str | None = "HTML") -> None:
  token = os.environ["TELEGRAM_BOT_TOKEN"]
  chat_id = os.environ["TELEGRAM_CHAT_ID"]
  url = f"https://api.telegram.org/bot{token}/sendMessage"
  body = {"chat_id": chat_id, "text": text}
  if parse_mode:
    body["parse_mode"] = parse_mode
  req = urllib.request.Request(
      url,
      data=json.dumps(body).encode("utf-8"),
      headers={"Content-Type": "application/json"},
  )
  try:
    with urllib.request.urlopen(req, timeout=15) as resp:
      resp.read()
  except urllib.error.HTTPError as exc:
    # Telegram-ის რეალური მიზეზი (მაგ. "chat not found", "can't parse entities")
    detail = exc.read().decode("utf-8", "replace")
    raise RuntimeError(f"Telegram HTTP {exc.code}: {detail}") from None
