import json
import os
import urllib.request


def send_telegram_message(text: str) -> None:
  token = os.environ["TELEGRAM_BOT_TOKEN"]
  chat_id = os.environ["TELEGRAM_CHAT_ID"]
  url = f"https://api.telegram.org/bot{token}/sendMessage"
  payload = json.dumps({
      "chat_id": chat_id,
      "text": text,
      "parse_mode": "HTML",
  }).encode("utf-8")
  req = urllib.request.Request(
      url, data=payload, headers={"Content-Type": "application/json"}
  )
  with urllib.request.urlopen(req, timeout=15) as resp:
    resp.read()
