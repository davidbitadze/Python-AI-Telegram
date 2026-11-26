from datetime import datetime, timedelta
from statistics import mean


def _parse_dt(dt_str: str | None) -> datetime | None:
  if not dt_str:
    return None
  for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
    try:
      return datetime.strptime(dt_str[:19], fmt)
    except ValueError:
      continue
  return None


def _filter_last_hours(candles: list[dict], hours: float = 36.0) -> list[dict]:
  """ტოვებს მხოლოდ ბოლო `hours` (ნაგულისხმებად 36 საათის) სანთლებს."""
  if not candles:
    return []
  parsed = [(c, _parse_dt(c.get("datetime"))) for c in candles]
  valid_dts = [dt for _, dt in parsed if dt is not None]
  if not valid_dts:
    max_bars = int(hours * 12)
    return candles[-max_bars:]
  latest_dt = max(valid_dts)
  cutoff = latest_dt - timedelta(hours=hours)
  return [c for c, dt in parsed if dt is not None and dt >= cutoff]


def _avg_range(candles: list[dict], lookback: int = 20) -> float:
  if not candles:
    return 0.0
  subset = candles[-lookback:]
  ranges = [max(0.0, c["high"] - c["low"]) for c in subset]
  return mean(ranges) if ranges else 0.0


def _has_early_impulse_and_closeback(
    arch_slice: list[dict], avg_rng: float, direction: str
) -> bool:
  """
    ამოწმებს, იყო თუ არა არკის დასაწყის 1-3 სანთელში იმპულსური გასროლა
    და შემდეგ უკან ჩახურვა/კორექცია.
    """
  early = arch_slice[:3]
  for c in early:
    rng = max(c["high"] - c["low"], 1e-9)
    body = abs(c["close"] - c["open"])
    is_impulse = rng >= avg_rng * 1.15 and (body / rng) >= 0.45
    if not is_impulse:
      continue
    if direction == "LONG" and c["close"] <= c["open"] + rng * 0.35:
      return True
    if direction == "SHORT" and c["close"] >= c["open"] - rng * 0.35:
      return True
  return False


def evaluate_volume_matrix(
    candles: list[dict], lookback: int, multiplier: float
) -> dict:
  """
    A.Volume SMA - Davit's Matrix v3.8 (REST API ადაპტაცია 1M lower-TF მოთხოვნების გარეშე):
    - 5M: lookback=200, multiplier=1.60
    - 30M: lookback=150, multiplier=1.50
    თუ volume მონაცემი არ მოდის (sma_vol == 0), იყენებს სანთლის დიაპაზონის/იმპულსის პროქსის.
    """
  if not candles:
    return {
        "above_threshold": False,
        "ratio": 0.0,
        "range_ratio": 0.0,
        "volume_source": "NO_DATA",
        "sma_vol": 0.0,
        "last_vol": 0.0,
        "candle_dir": "FLAT",
        "body_strength": 0.0,
        "proxy_delta_hint": "NO_DATA",
    }

  last = candles[-1]
  last_vol = float(last.get("volume") or 0.0)
  hist = candles[-(lookback + 1) : -1] if len(candles) > 1 else []
  vols = [
      float(c.get("volume") or 0.0)
      for c in hist
      if (c.get("volume") or 0.0) > 0
  ]

  if last["close"] > last["open"]:
    candle_dir = "BULLISH"
  elif last["close"] < last["open"]:
    candle_dir = "BEARISH"
  else:
    candle_dir = "FLAT"

  rng = max(last["high"] - last["low"], 1e-9)
  body = last["close"] - last["open"]
  body_strength = round(abs(body) / rng, 2)
  avg_rng = _avg_range(candles[:-1] if len(candles) > 1 else candles, lookback=20)
  range_ratio = round(rng / avg_rng, 2) if avg_rng > 0 else 0.0

  sma_vol = mean(vols) if vols else 0.0
  if sma_vol > 0:
    threshold_vol = sma_vol * multiplier
    above_threshold = last_vol >= threshold_vol
    ratio = round(last_vol / sma_vol, 2)
    volume_source = "VOL_SMA"
    if not above_threshold:
      proxy_hint = "BELOW_THRESHOLD"
    elif candle_dir == "BULLISH" and body_strength >= 0.45:
      proxy_hint = "STRONG_BULL_IMPULSE_VERIFY_TV_CIRCLE"
    elif candle_dir == "BEARISH" and body_strength >= 0.45:
      proxy_hint = "STRONG_BEAR_IMPULSE_VERIFY_TV_CIRCLE"
    else:
      proxy_hint = "MIXED_WICK_OR_WEAK_BODY_VERIFY_TV_CIRCLE"
  else:
    # Fallback როცა Twelve Data volume-ს არ აბრუნებს: ფასის დიაპაზონის + სხეულის იმპულსი
    above_threshold = range_ratio >= 1.35 and body_strength >= 0.50
    ratio = 0.0
    volume_source = "RANGE_PROXY"
    if candle_dir == "BULLISH" and above_threshold:
      proxy_hint = "PROXY_STRONG_BULL_RANGE_VERIFY_TV_CIRCLE"
    elif candle_dir == "BEARISH" and above_threshold:
      proxy_hint = "PROXY_STRONG_BEAR_RANGE_VERIFY_TV_CIRCLE"
    elif range_ratio >= 1.15:
      proxy_hint = "PROXY_MODERATE_RANGE_VERIFY_TV_CIRCLE"
    else:
      proxy_hint = "PROXY_LOW_RANGE_VERIFY_TV_CIRCLE"

  return {
      "above_threshold": above_threshold,
      "ratio": ratio,
      "range_ratio": range_ratio,
      "volume_source": volume_source,
      "multiplier": multiplier,
      "lookback": lookback,
      "sma_vol": round(sma_vol, 2),
      "last_vol": round(last_vol, 2),
      "candle_dir": candle_dir,
      "body_strength": body_strength,
      "proxy_delta_hint": proxy_hint,
  }


def infer_30m_bias(candles_30m: list[dict], lookback: int = 24) -> str:
  """30M ტაიმფრეიმის პრიორიტეტული მიმართულება (BULLISH / BEARISH / RANGE)."""
  if len(candles_30m) < lookback:
    return "RANGE"
  recent = candles_30m[-lookback:]
  first_half = recent[: lookback // 2]
  second_half = recent[lookback // 2 :]

  high_1 = max(c["high"] for c in first_half)
  low_1 = min(c["low"] for c in first_half)
  high_2 = max(c["high"] for c in second_half)
  low_2 = min(c["low"] for c in second_half)
  last_close = recent[-1]["close"]

  if (
      high_2 > high_1
      and low_2 >= low_1
      and last_close > mean(c["close"] for c in first_half)
  ):
    return "BULLISH"
  if (
      low_2 < low_1
      and high_2 <= high_1
      and last_close < mean(c["close"] for c in first_half)
  ):
    return "BEARISH"
  return "RANGE"


def confirm_3h_alignment(candles_3h: list[dict], bias_30m: str) -> bool:
  """3H ადასტურებს 30M-ის ხედვას."""
  if bias_30m == "RANGE":
    return True
  if len(candles_3h) < 4:
    return True
  recent_3h = candles_3h[-4:]
  net_move = recent_3h[-1]["close"] - recent_3h[0]["open"]
  if bias_30m == "BULLISH":
    return net_move >= 0
  if bias_30m == "BEARISH":
    return net_move <= 0
  return True


def _detect_at(
    candles_5m: list[dict],
    candles_30m: list[dict],
    bias_30m: str,
    aligned_3h: bool,
    min_pattern_candles: int = 14,
    arch_mult_vs_first: float = 2.5,
    max_pierce_mult: float = 0.35,
    max_age_hours: float = 36.0,
) -> dict | None:
  """
    5M ეტაპი (მხოლოდ 30M + 3H კონტექსტის შემდეგ):
    1) ფანჯარა იზღუდება ბოლო `max_age_hours` (36 საათით).
    2) საწყისი მიბჯენა -> არკის ფორმირება (>= 2.5x საწყისი სანთლის დიაპაზონთან) -> მეორე მიბჯენა დონეზე.
    3) მთლიანი სტრუქტურა: მინიმუმ 14 სანთელი, ღრმა ჩახვრეტის გარეშე.
    """
  recent_5m = _filter_last_hours(candles_5m, hours=max_age_hours)
  if not aligned_3h or len(recent_5m) < max(20, min_pattern_candles + 2):
    return None

  avg_rng = _avg_range(recent_5m, lookback=20)
  if avg_rng <= 0:
    return None

  last = recent_5m[-1]
  last_body = abs(last["close"] - last["open"])
  last_range = max(last["high"] - last["low"], 1e-9)

  matrix_5m = evaluate_volume_matrix(candles_5m, lookback=200, multiplier=1.60)
  matrix_30m = evaluate_volume_matrix(candles_30m, lookback=150, multiplier=1.50)

  found: dict | None = None
  window = recent_5m[-min(len(recent_5m), 96) :]
  n = len(window)

  # 1. Double Bottom (LONG)
  if last["close"] > last["open"] and last_range >= avg_rng * 0.8:
    best_bottom = None
    for start_idx in range(0, n - min_pattern_candles + 1):
      first_c = window[start_idx]
      first_rng = max(first_c["high"] - first_c["low"], 1e-9)
      base_level = first_c["low"]
      max_pierce = max(first_rng * max_pierce_mult, avg_rng * max_pierce_mult)

      if last["low"] < (base_level - max_pierce):
        continue

      min_retest_idx = start_idx + min_pattern_candles - 1
      for retest_idx in range(min_retest_idx, n):
        arch_slice = window[start_idx + 1 : retest_idx]
        if not arch_slice:
          continue
        arch_peak = max(c["high"] for c in arch_slice)
        arch_span = arch_peak - base_level
        if arch_span < first_rng * arch_mult_vs_first:
          continue

        retest_c = window[retest_idx]
        if any(
            c["low"] < (base_level - max_pierce)
            for c in window[start_idx + 1 : retest_idx + 1]
        ):
          continue

        retest_dist = abs(retest_c["low"] - base_level)
        if retest_dist <= max(first_rng * 0.5, avg_rng * 0.5):
          total_len = retest_idx - start_idx + 1
          early_imp_close = _has_early_impulse_and_closeback(
              arch_slice, avg_rng, "LONG"
          )
          score = (arch_span / first_rng) + (0.25 if early_imp_close else 0.0)
          if best_bottom is None or score > best_bottom[0]:
            best_bottom = (
                score,
                base_level,
                retest_c["low"],
                total_len,
                round(arch_span / first_rng, 2),
                first_c["datetime"],
                early_imp_close,
            )

    if best_bottom is not None:
      _, base_lvl, retest_lvl, total_len, arch_mult_actual, start_dt, early_imp_close = (
          best_bottom
      )
      found = {
          "pattern": "DOUBLE_BOTTOM",
          "direction": "LONG",
          "bias_30m": bias_30m,
          "level": round(mean([base_lvl, retest_lvl]), 5),
          "arch_start_dt": start_dt,
          "pattern_candles": total_len,
          "arch_vs_first_mult": arch_mult_actual,
          "early_impulse_closeback": early_imp_close,
          "max_age_hours": max_age_hours,
          "last_candle": last,
          "impulse_ratio": round(last_body / last_range, 2),
      }

  # 2. Double Top (SHORT)
  if not found and last["close"] < last["open"] and last_range >= avg_rng * 0.8:
    best_top = None
    for start_idx in range(0, n - min_pattern_candles + 1):
      first_c = window[start_idx]
      first_rng = max(first_c["high"] - first_c["low"], 1e-9)
      base_level = first_c["high"]
      max_pierce = max(first_rng * max_pierce_mult, avg_rng * max_pierce_mult)

      if last["high"] > (base_level + max_pierce):
        continue

      min_retest_idx = start_idx + min_pattern_candles - 1
      for retest_idx in range(min_retest_idx, n):
        arch_slice = window[start_idx + 1 : retest_idx]
        if not arch_slice:
          continue
        arch_trough = min(c["low"] for c in arch_slice)
        arch_span = base_level - arch_trough
        if arch_span < first_rng * arch_mult_vs_first:
          continue

        retest_c = window[retest_idx]
        if any(
            c["high"] > (base_level + max_pierce)
            for c in window[start_idx + 1 : retest_idx + 1]
        ):
          continue

        retest_dist = abs(retest_c["high"] - base_level)
        if retest_dist <= max(first_rng * 0.5, avg_rng * 0.5):
          total_len = retest_idx - start_idx + 1
          early_imp_close = _has_early_impulse_and_closeback(
              arch_slice, avg_rng, "SHORT"
          )
          score = (arch_span / first_rng) + (0.25 if early_imp_close else 0.0)
          if best_top is None or score > best_top[0]:
            best_top = (
                score,
                base_level,
                retest_c["high"],
                total_len,
                round(arch_span / first_rng, 2),
                first_c["datetime"],
                early_imp_close,
            )

    if best_top is not None:
      _, base_lvl, retest_lvl, total_len, arch_mult_actual, start_dt, early_imp_close = (
          best_top
      )
      found = {
          "pattern": "DOUBLE_TOP",
          "direction": "SHORT",
          "bias_30m": bias_30m,
          "level": round(mean([base_lvl, retest_lvl]), 5),
          "arch_start_dt": start_dt,
          "pattern_candles": total_len,
          "arch_vs_first_mult": arch_mult_actual,
          "early_impulse_closeback": early_imp_close,
          "max_age_hours": max_age_hours,
          "last_candle": last,
          "impulse_ratio": round(last_body / last_range, 2),
      }

  if not found:
    return None

  found["matrix_5m"] = matrix_5m
  found["matrix_30m"] = matrix_30m
  return found


def detect_5m_candidate(
    candles_5m: list[dict],
    candles_30m: list[dict],
    bias_30m: str,
    aligned_3h: bool,
    lookback_candles: int = 6,
) -> dict | None:
  """
    ამოწმებს ბოლო `lookback_candles` ხუთწუთიან სანთელს (6 = 30 წუთი), რადგან
    სკანირება ყოველ 30 წუთში ერთხელ ხდება. აბრუნებს ყველაზე ახალ პატერნს.
    დუბლიკატებს main.py-ში alert_key (წყვილი+პატერნი+სანთლის დრო) აფილტრავს.
    """
  for k in range(lookback_candles):
    subset = candles_5m[: len(candles_5m) - k]
    found = _detect_at(subset, candles_30m, bias_30m, aligned_3h)
    if found:
      found["candles_ago"] = k
      return found
  return None
