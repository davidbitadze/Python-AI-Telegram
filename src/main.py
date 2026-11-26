from datetime import datetime, timedelta
import html
import os
import time
from urllib.error import HTTPError

from ai_evaluator import evaluate_candidate
from alert_state import (
    build_alert_key,
    build_cooldown_key,
    build_daily_level_key,
    is_duplicate_alert,
    is_in_cooldown,
    load_state,
    mark_alert_sent,
    prune_old_daily_keys,
    prune_old_entries,
    save_state,
)
from config import PAIRS, TBILISI_TZ, is_within_entry_window
from market_calendar import check_market_holidays, is_weekend
from market_data import RateLimitError, fetch_candles
from notifier import send_telegram_message
from technical_filter import (
    _avg_range,
    confirm_3h_alignment,
    detect_5m_candidate,
    evaluate_volume_matrix,
    infer_30m_bias,
)

REQUEST_DELAY_SEC = 8.0
ALLOWED_PATTERNS = {"DOUBLE_TOP", "DOUBLE_BOTTOM"}
MAX_AI_EVALS_PER_RUN = int(os.environ.get("MAX_AI_EVALS_PER_RUN", "4"))
COOLDOWN_MINUTES = 120  # იგივე წყვილი+მიმართულება არაუმეტეს 2 საათში ერთხელ
HEALTH_ERROR_THRESHOLD = 5  # ამდენ წყვილზე მონაცემის შეცდომისას -> health alert


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
    except RateLimitError as exc:
      if not exc.daily and attempt < max_retries:
        print(
            f"[{pair} {interval}] Twelve Data rate limit. Waiting {backoff_sec}s "
            f"(retry {attempt + 1}/{max_retries})..."
        )
        time.sleep(backoff_sec)
        continue
      raise
    except HTTPError as exc:
      if exc.code == 429 and attempt < max_retries:
        print(
            f"[{pair} {interval}] HTTP 429 rate limit hit. Waiting {backoff_sec}s "
            f"(retry {attempt + 1}/{max_retries})..."
        )
        time.sleep(backoff_sec)
        continue
      raise


def _parse_candle_dt(dt_str: str) -> datetime | None:
  try:
    return datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
  except (ValueError, TypeError):
    return None


def drop_forming_candle(
    candles: list[dict], now_tbilisi: datetime, minutes: int
) -> list[dict]:
  """
    Twelve Data ბოლოს მიმდინარე (ჯერ დაუხურავ) სანთელსაც აბრუნებს. ასეთი სანთელი
    სიგნალად არ გამოდგება (მაგ. 19:00 სანთელი 19:00:55-ზე უკვე „იმპულსად“ ჩაითვალა).
    დრო თბილისის ზონაშია (market_data-ში timezone=Asia/Tbilisi).
    """
  if not candles:
    return candles
  last_dt = _parse_candle_dt(candles[-1]["datetime"])
  if last_dt is None:
    return candles
  age = now_tbilisi.replace(tzinfo=None) - last_dt
  if timedelta(0) <= age < timedelta(minutes=minutes):
    return candles[:-1]
  return candles


def signal_age_minutes(candidate: dict, now_tbilisi: datetime) -> int:
  """რამდენი წუთია, რაც სიგნალის 5M სანთელი დაიხურა."""
  dt = _parse_candle_dt(candidate["last_candle"]["datetime"])
  if dt is None:
    return candidate.get("candles_ago", 0) * 5
  closed_at = dt + timedelta(minutes=5)
  return max(0, int((now_tbilisi.replace(tzinfo=None) - closed_at).total_seconds() // 60))


def format_matrix_line(m5: dict, m30: dict) -> str:
  if m5.get("volume_source") == "RANGE_PROXY":
    return (
        f"5M იმპულსი (დიაპაზონის პროქსი, volume არ მოდის): "
        f"<b>{'YES' if m5['above_threshold'] else 'NO'}</b> "
        f"(დიაპაზონი x{m5.get('range_ratio')})"
    )
  return (
      f"5M Matrix (200/1.60): <b>{'YES' if m5['above_threshold'] else 'NO'}</b> "
      f"(x{m5['ratio']}) | 30M (150/1.50): x{m30['ratio']}"
  )


def check_watch_mode(
    candles_5m: list[dict], candles_30m: list[dict], bias_30m: str
) -> str | None:
  """მოლოდინის რეჟიმი (AI-ს და ტელეგრამ-სპამის გარეშე)."""
  if len(candles_5m) < 25:
    return None
  avg_rng = _avg_range(candles_5m, lookback=20)
  if avg_rng <= 0:
    return None
  m5 = evaluate_volume_matrix(candles_5m, lookback=200, multiplier=1.60)
  m30 = evaluate_volume_matrix(candles_30m, lookback=150, multiplier=1.50)
  vol_notable = bool(
      m5.get("above_threshold")
      or m30.get("above_threshold")
      or float(m5.get("ratio") or 0.0) >= 1.5
  )
  if not vol_notable:
    return None

  last = candles_5m[-1]
  recent_high = max(c["high"] for c in candles_5m[-25:-1])
  recent_low = min(c["low"] for c in candles_5m[-25:-1])
  prox_threshold = avg_rng * 0.6

  if abs(last["high"] - recent_high) <= prox_threshold:
    return (
        f"WATCH_DOUBLE_TOP_PROX (bias={bias_30m}, level={round(recent_high, 5)}, "
        f"5M_vol=x{m5.get('ratio')}, 30M_vol=x{m30.get('ratio')})"
    )
  if abs(last["low"] - recent_low) <= prox_threshold:
    return (
        f"WATCH_DOUBLE_BOTTOM_PROX (bias={bias_30m}, level={round(recent_low, 5)}, "
        f"5M_vol=x{m5.get('ratio')}, 30M_vol=x{m30.get('ratio')})"
    )
  return None


def build_3h_from_1h(candles_1h: list[dict]) -> list[dict]:
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
  cand = item["candidate"]
  m5 = cand.get("matrix_5m", {})
  vol_ok = 1 if m5.get("above_threshold") else 0
  ratio = float(m5.get("ratio") or 0.0)
  impulse = float(cand.get("impulse_ratio") or 0.0)
  return (vol_ok, ratio, impulse)


def send_ai_down_alert(item: dict, reason: str) -> bool:
  cand = item["candidate"]
  now = datetime.now(TBILISI_TZ).strftime("%H:%M")
  msg = (
      f"⚠️ AI მიუწვდომელია, ტექნიკური სიგნალი:\n"
      f"<b>{item['pair']}</b> | {cand['pattern']} ({cand['direction']})\n"
      f"30M Bias: <b>{item['bias_30m']}</b> | Level: <b>{cand['level']}</b>\n"
      f"სიგნალის სანთელი: {cand['last_candle']['datetime']} "
      f"({signal_age_minutes(cand, datetime.now(TBILISI_TZ))} წთ-ის წინ დაიხურა)\n"
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
  today_str = now_tbilisi.strftime("%Y-%m-%d")
  force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"
  weekend = is_weekend(now_tbilisi)
  in_window = is_within_entry_window(now_tbilisi)
  uk_closed, us_closed, holiday_status = check_market_holidays(now_tbilisi.date())

  print(
      f"=== SCAN START | Tbilisi: {now_tbilisi.strftime('%Y-%m-%d %H:%M:%S %Z')} "
      f"(weekday={now_tbilisi.weekday()}, weekend={weekend}) | in_window={in_window} "
      f"| market_status={holiday_status} | force_run={force_run} ==="
  )

  if not force_run:
    if weekend or not in_window:
      print("Weekend or outside entry window (13:00-21:30 Tbilisi). Skipping.")
      return
    if uk_closed and us_closed:
      print(
          f"Both London (LSE) and New York (NYSE) markets are closed ({holiday_status}). "
          "Skipping scan and AI calls."
      )
      return

  state, state_changed = prune_old_daily_keys(load_state(), today_str)
  state, pruned = prune_old_entries(state, now_tbilisi)
  state_changed = state_changed or pruned

  scanned_count = 0
  candidates_count = 0
  notified_count = 0
  ai_evals_used = 0
  quota_exhausted = False
  fetch_errors = 0
  last_fetch_error = ""
  queued_for_ai: list[dict] = []

  for pair in PAIRS:
    print(f"\n--- [{pair}] ---")
    try:
      candles_30m = fetch_with_retry(pair, "30min", outputsize=160)
      candles_30m = drop_forming_candle(candles_30m, now_tbilisi, minutes=30)
      bias_30m = infer_30m_bias(candles_30m)

      candles_1h = fetch_with_retry(pair, "1h", outputsize=45)
      candles_1h = drop_forming_candle(candles_1h, now_tbilisi, minutes=60)
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
      candles_5m = drop_forming_candle(candles_5m, now_tbilisi, minutes=5)
    except Exception as exc:
      print(f"[{pair}] ERROR fetching data: {exc}")
      fetch_errors += 1
      last_fetch_error = str(exc)
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
      watch_note = check_watch_mode(candles_5m, candles_30m, bias_30m)
      if watch_note:
        print(f"[{pair}] Result: NO_CANDIDATE | {watch_note}")
      else:
        print(f"[{pair}] Result: NO_CANDIDATE")
      continue

    pattern_name = candidate.get("pattern", "")
    if pattern_name not in ALLOWED_PATTERNS:
      print(f"[{pair}] Result: FILTERED_PATTERN ({pattern_name})")
      continue

    candidates_count += 1
    m5 = candidate["matrix_5m"]
    last_dt = candidate["last_candle"]["datetime"]
    alert_key = build_alert_key(
        pair, pattern_name, candidate["direction"], last_dt
    )
    daily_key = build_daily_level_key(
        today_str,
        pair,
        pattern_name,
        candidate["direction"],
        candidate["level"],
    )

    print(
        f"[{pair}] CANDIDATE found: pattern={pattern_name}, dir={candidate['direction']}, "
        f"level={candidate['level']}, impulse={candidate.get('impulse_ratio')}, "
        f"5M_vol_above={m5['above_threshold']} (x{m5['ratio']}), candle_dt={last_dt}"
    )

    cooldown_key = build_cooldown_key(pair, candidate["direction"])
    if is_in_cooldown(state, cooldown_key, now_tbilisi, COOLDOWN_MINUTES):
      print(f"[{pair}] Result: SKIPPED_COOLDOWN ({COOLDOWN_MINUTES} წთ, key={cooldown_key})")
      continue

    if is_duplicate_alert(state, alert_key, daily_key):
      print(
          f"[{pair}] Result: SKIPPED_DUPLICATE (alert_key={alert_key}, daily_key={daily_key})"
      )
      continue

    queued_for_ai.append({
        "pair": pair,
        "bias_30m": bias_30m,
        "candidate": candidate,
        "alert_key": alert_key,
        "daily_key": daily_key,
        "cooldown_key": cooldown_key,
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
        mark_alert_sent(
            state,
            item["alert_key"],
            item["daily_key"],
            now_tbilisi.isoformat(),
            item["cooldown_key"],
        )
        state_changed = True
        notified_count += 1
        print(f"[{item['pair']}] Result: SENT_TELEGRAM (AI down)")
      continue

    pair = item["pair"]
    bias_30m = item["bias_30m"]
    candidate = item["candidate"]
    alert_key = item["alert_key"]
    daily_key = item["daily_key"]
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
        mark_alert_sent(
            state, alert_key, daily_key, now_tbilisi.isoformat(), item["cooldown_key"]
        )
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
          f"{format_matrix_line(m5, m30)}\n"
          f"სიგნალის სანთელი: {last_dt} "
          f"({signal_age_minutes(candidate, datetime.now(TBILISI_TZ))} წთ-ის წინ დაიხურა)\n"
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

      mark_alert_sent(
          state, alert_key, daily_key, now_tbilisi.isoformat(), item["cooldown_key"]
      )
      state_changed = True
      notified_count += 1
      print(f"[{pair}] Result: SENT_TELEGRAM")
    else:
      print(f"[{pair}] Result: NOT_NOTIFIED_BY_AI")

  if fetch_errors >= HEALTH_ERROR_THRESHOLD:
    health_key = f"DAILY_{today_str}_HEALTH_{now_tbilisi.strftime('%H')}"
    if not state.get(health_key):
      try:
        send_telegram_message(
            f"🛠 სკანერის პრობლემა: {fetch_errors}/{len(PAIRS)} წყვილზე მონაცემები "
            f"ვერ წამოვიდა.\nბოლო შეცდომა: {html.escape(last_fetch_error[:300], quote=False)}"
        )
        state[health_key] = now_tbilisi.isoformat()
        state_changed = True
      except Exception as exc:
        print(f"ERROR sending health alert: {exc}")

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
