from datetime import datetime
import html
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError

from ai_evaluator import evaluate_candidate
from config import PAIRS, TBILISI_TZ, is_within_entry_window
from market_data import fetch_candles
from notifier import send_telegram_message
from technical_filter import confirm_3h_alignment, detect_5m_candidate, infer_30m_bias

STATE_FILE = Path("state/sent_alerts.json")
REQUEST_DELAY_SEC = 8.0  # Twelve Data free: 8 მოთხოვნა/წუთში
ALLOWED_PATTERNS = {"DOUBLE_TOP", "DOUBLE_BOTTOM"}
MAX_AI_EVALS_PER_RUN = 2  # დღიური ლიმიტის დასაზოგად


def fetch_with_retry(
    pair: str,
    interval: str,
    outputsize: int,
    max_retries: int = 2,
    backoff_sec: float = 65.0,
) -> list[dict]:
  for attempt in range(max_retries + 1):
    try:
      candles = fetch_candles(pair, interval, outputsize=outputsize)
      time.sleep(REQUEST_DELAY_SEC)
      return candles
    except HTTPError as exc:
      if exc.code == 429 and attempt < max_retries:
        print(
            f"[{pair} {interval}] HTTP 429 rate limit hit. Waiting {backoff_sec}s "
            f"(retry {attempt + 1}/{max_retries})..."
        )
        time.sleep(backoff_sec)
        continue
      raise


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


def run_telegram_test() -> None:
  now = datetime.now(TBILISI_TZ).strftime("%Y-%m-%d %H:%M")
  send_telegram_message(
      f"✅ სატესტო შეტყობინება\nForex AI Scanner მუშაობს.\nდრო: {now} (თბილისი)",
      parse_mode=None,
  )
  print("Test message sent to Telegram.")


def candidate_priority(item: dict) -> tuple:
  """პრიორიტეტს ანიჭებს 5M მოცულობის მქონე და უფრო ძლიერ კანდიდატებს."""
  cand = item["candidate"]
  m5 = cand.get("matrix_5m", {})
  vol_ok = 1 if m5.get("above_threshold") else 0
  ratio = float(m5.get("ratio") or 0.0)
  impulse = float(cand.get("impulse_ratio") or 0.0)
  return (vol_ok, ratio, impulse)


def send_ai_down_alert(item: dict, reason: str) -> bool:
  """AI მიუწვდომელია -> მოკლე ტექნიკური შეტყობინება, რომ სიგნალი არ დაიკარგოს."""
  cand = item["candidate"]
  now = datetime.now(TBILISI_TZ).strftime("%H:%M")
  msg = (
      f"⚠️ AI მიუწვდომელია, ტექნიკური სიგნალი:\n"
      f"<b>{item['pair']}</b> | {cand['pattern']} ({cand['direction']})\n"
      f"30M Bias: <b>{item['bias_30m']}</b> | Level: <b>{cand['level']}</b>\n"
      f"სიგნალის სანთელი: {cand['last_candle']['datetime']} "
      f"({cand.get('candles_ago', 0) * 5} წთ-ის წინ)\n"
      f"დრო: {now} (თბილისი)\n"
      f"მიზეზი: {html.escape(reason)}"
  )
  try:
    send_telegram_message(msg)
    return True
  except Exception as exc:
    print(f"[{item['pair']}] ERROR sending AI-down Telegram: {exc}")
    return False


def main() -> None:
  if os.environ.get("TEST_TELEGRAM", "false").lower() == "true":
    run_telegram_test()
    return

  now_tbilisi = datetime.now(TBILISI_TZ)
  force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"
  in_window = is_within_entry_window(now_tbilisi)

  print(
      f"=== SCAN START | Tbilisi: {now_tbilisi.strftime('%Y-%m-%d %H:%M:%S %Z')} "
      f"(weekday={now_tbilisi.weekday()}) | in_window={in_window} | force_run={force_run} ==="
  )

  if not force_run and not in_window:
    print("Outside entry window (13:00-21:30 Tbilisi or weekend). Skipping.")
    return

  state = load_state()
  state_changed = False

  scanned_count = 0
  candidates_count = 0
  notified_count = 0
  ai_evals_used = 0
  quota_exhausted = False
  queued_for_ai: list[dict] = []

  for pair in PAIRS:
    print(f"\n--- [{pair}] ---")
    try:
      candles_30m = fetch_with_retry(pair, "30min", outputsize=160)
      bias_30m = infer_30m_bias(candles_30m)

      candles_1h = fetch_with_retry(pair, "1h", outputsize=45)
      candles_3h = build_3h_from_1h(candles_1h)
      aligned_3h = confirm_3h_alignment(candles_3h, bias_30m)

      if not aligned_3h:
        scanned_count += 1
        print(
            f"[{pair}] bars=(30m:{len(candles_30m)}, 3h:{len(candles_3h)}) "
            f"| 30M_bias={bias_30m} | 3H_aligned=False -> SKIP_5M_FETCH"
        )
        continue

      candles_5m = fetch_with_retry(pair, "5min", outputsize=210)
    except Exception as exc:
      print(f"[{pair}] ERROR fetching data: {exc}")
      continue

    scanned_count += 1
    candidate = detect_5m_candidate(
        candles_5m=candles_5m,
        candles_30m=candles_30m,
        bias_30m=bias_30m,
        aligned_3h=aligned_3h,
    )

    print(
        f"[{pair}] bars=(5m:{len(candles_5m)}, 30m:{len(candles_30m)}, 3h:{len(candles_3h)}) "
        f"| 30M_bias={bias_30m} | 3H_aligned={aligned_3h}"
    )

    if not candidate:
      print(f"[{pair}] Result: NO_CANDIDATE")
      continue

    pattern_name = candidate.get("pattern", "")
    if pattern_name not in ALLOWED_PATTERNS:
      print(f"[{pair}] Result: FILTERED_PATTERN ({pattern_name})")
      continue

    candidates_count += 1
    m5 = candidate["matrix_5m"]
    last_dt = candidate["last_candle"]["datetime"]
    alert_key = f"{pair}_{pattern_name}_{candidate['direction']}_{last_dt}"

    print(
        f"[{pair}] CANDIDATE found: pattern={pattern_name}, dir={candidate['direction']}, "
        f"level={candidate['level']}, impulse={candidate.get('impulse_ratio')}, "
        f"5M_vol_above={m5['above_threshold']} (x{m5['ratio']}), candle_dt={last_dt}"
    )

    if state.get(alert_key):
      print(f"[{pair}] Result: SKIPPED_DUPLICATE (key={alert_key})")
      continue

    queued_for_ai.append({
        "pair": pair,
        "bias_30m": bias_30m,
        "candidate": candidate,
        "alert_key": alert_key,
    })

  queued_for_ai.sort(key=candidate_priority, reverse=True)
  selected_for_ai = queued_for_ai[:MAX_AI_EVALS_PER_RUN]
  skipped_cap = queued_for_ai[MAX_AI_EVALS_PER_RUN:]

  for item in skipped_cap:
    print(f"[{item['pair']}] Result: SKIPPED_AI_RUN_CAP (max={MAX_AI_EVALS_PER_RUN})")

  for item in selected_for_ai:
    if quota_exhausted:
      print(f"[{item['pair']}] Result: QUOTA_EXHAUSTED -> technical alert")
      if send_ai_down_alert(item, "Gemini-ს კვოტა ამოიწურა"):
        state[item["alert_key"]] = now_tbilisi.isoformat()
        state_changed = True
        notified_count += 1
        print(f"[{item['pair']}] Result: SENT_TELEGRAM (AI down)")
      continue

    pair = item["pair"]
    bias_30m = item["bias_30m"]
    candidate = item["candidate"]
    alert_key = item["alert_key"]
    m5 = candidate["matrix_5m"]
    m30 = candidate["matrix_30m"]
    last_dt = candidate["last_candle"]["datetime"]

    ai_evals_used += 1
    try:
      evaluation = evaluate_candidate(
          pair=pair,
          candidate=candidate,
          tbilisi_now_str=now_tbilisi.strftime("%Y-%m-%d %H:%M"),
      )
    except Exception as exc:
      err_str = str(exc)
      print(f"[{pair}] ERROR in AI evaluator: {err_str}")
      if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
        quota_exhausted = True
      short = "კვოტა ამოიწურა" if quota_exhausted else "დროებითი შეცდომა"
      if send_ai_down_alert(item, f"Gemini: {short}"):
        state[alert_key] = now_tbilisi.isoformat()
        state_changed = True
        notified_count += 1
        print(f"[{pair}] Result: SENT_TELEGRAM (AI down)")
      continue

    notify_flag = bool(evaluation.get("notify"))
    score = evaluation.get("quality_score", 0)
    summary = evaluation.get("setup_summary", "")
    print(
        f"[{pair}] AI_EVAL: notify={notify_flag}, score={score}/10, summary={summary}"
    )

    if notify_flag:
      msg = (
          f"<b>{pair}</b> | {candidate['pattern']} ({candidate['direction']})\n"
          f"30M Bias: <b>{bias_30m}</b> | Level: <b>{candidate['level']}</b>\n"
          f"5M Matrix (200/1.60): <b>{'YES' if m5['above_threshold'] else 'NO'}</b> "
          f"(x{m5['ratio']}) | 30M (150/1.50): x{m30['ratio']}\n"
          f"სიგნალის სანთელი: {last_dt} ({candidate.get('candles_ago', 0) * 5} წთ-ის წინ)\n"
          f"TV Circle / 1M Delta: <i>გადასამოწმებელია TradingView-ზე</i>\n"
          f"Score: <b>{score}/10</b>\n"
          f"დრო: {datetime.now(TBILISI_TZ).strftime('%H:%M')} (თბილისი)\n"
          f"ანალიზი: {html.escape(summary)}\n"
          f"⚠️ რისკი/შენიშვნა: {html.escape(evaluation.get('risk_and_caution', ''))}"
      )
      try:
        send_telegram_message(msg)
      except Exception as exc:
        print(f"[{pair}] ERROR sending Telegram: {exc}")
        continue

      state[alert_key] = now_tbilisi.isoformat()
      state_changed = True
      notified_count += 1
      print(f"[{pair}] Result: SENT_TELEGRAM")
    else:
      print(f"[{pair}] Result: NOT_NOTIFIED_BY_AI")

  if state_changed:
    save_state(state)
    print("\nAlert state updated and saved.")

  print(
      f"\n=== SCAN SUMMARY | scanned={scanned_count}/{len(PAIRS)} "
      f"| candidates={candidates_count} | ai_evals={ai_evals_used} "
      f"| notified={notified_count} ==="
  )


if __name__ == "__main__":
  main()
