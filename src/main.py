from datetime import datetime
import json
import os
from pathlib import Path
import time

from ai_evaluator import evaluate_candidate
from config import PAIRS, TBILISI_TZ, is_within_entry_window
from market_data import fetch_candles
from notifier import send_telegram_message
from technical_filter import confirm_3h_alignment, detect_5m_candidate, infer_30m_bias

STATE_FILE = Path("state/sent_alerts.json")


def load_state() -> dict:
  if not STATE_FILE.exists():
    return {}
  return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
  STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
  STATE_FILE.write_text(
      json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
  )


def build_3h_from_1h(candles_1h: list[dict]) -> list[dict]:
  """1H სანთლებისგან აწყობს 3H ბლოკებს."""
  blocks = []
  for i in range(0, len(candles_1h) - 2, 3):
    chunk = candles_1h[i : i + 3]
    blocks.append({
        "datetime": chunk[0]["datetime"],
        "open": chunk[0]["open"],
        "high": max(c["high"] for c in chunk),
        "low": min(c["low"] for c in chunk),
        "close": chunk[-1]["close"],
        "volume": sum(c["volume"] for c in chunk),
    })
  return blocks


def main() -> None:
  now_tbilisi = datetime.now(TBILISI_TZ)
  force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"

  if not force_run and not is_within_entry_window(now_tbilisi):
    print(f"Outside entry window ({now_tbilisi.strftime('%Y-%m-%d %H:%M %Z')}). Skipping.")
    return

  state = load_state()
  state_changed = False

  for pair in PAIRS:
    try:
      candles_5m = fetch_candles(pair, "5min", outputsize=60)
      time.sleep(1.0)
      candles_30m = fetch_candles(pair, "30min", outputsize=60)
      time.sleep(1.0)
      candles_1h = fetch_candles(pair, "1h", outputsize=45)
      time.sleep(1.0)
    except Exception as exc:
      print(f"[ERROR] {pair}: {exc}")
      continue

    bias_30m = infer_30m_bias(candles_30m)
    candles_3h = build_3h_from_1h(candles_1h)
    aligned_3h = confirm_3h_alignment(candles_3h, bias_30m)
    candidate = detect_5m_candidate(candles_5m, bias_30m, aligned_3h)

    if not candidate:
      continue

    last_dt = candidate["last_candle"]["datetime"]
    alert_key = f"{pair}_{candidate['pattern']}_{candidate['direction']}_{last_dt}"
    if state.get(alert_key):
      continue

    evaluation = evaluate_candidate(
        pair=pair,
        candidate=candidate,
        tbilisi_now_str=now_tbilisi.strftime("%Y-%m-%d %H:%M"),
    )

    if evaluation.get("notify"):
      msg = (
          f"<b>{pair}</b> | {candidate['pattern']} ({candidate['direction']})\n"
          f"30M Bias: <b>{bias_30m}</b> | Level: <b>{candidate['level']}</b>\n"
          f"Score: <b>{evaluation.get('quality_score', 0)}/10</b>\n"
          f"დრო: {now_tbilisi.strftime('%H:%M')} (თბილისი)\n"
          f"ანალიზი: {evaluation.get('setup_summary', '')}\n"
          f"⚠️ რისკი/შენიშვნა: {evaluation.get('risk_and_caution', '')}"
      )
      send_telegram_message(msg)
      state[alert_key] = now_tbilisi.isoformat()
      state_changed = True

  if state_changed:
    save_state(state)


if __name__ == "__main__":
  main()
