from statistics import mean


def _avg_range(candles: list[dict], lookback: int = 20) -> float:
  if not candles:
    return 0.0
  subset = candles[-lookback:]
  ranges = [max(0.0, c["high"] - c["low"]) for c in subset]
  return mean(ranges) if ranges else 0.0


def evaluate_volume_matrix(
    candles: list[dict], lookback: int, multiplier: float
) -> dict:
  """
    A.Volume SMA - Davit's Matrix v3.8 (REST API ადაპტაცია 1M lower-TF მოთხოვნების გარეშე):
    - 5M: lookback=200, multiplier=1.60
    - 30M: lookback=150, multiplier=1.50
    """
  if not candles:
    return {
        "above_threshold": False,
        "ratio": 0.0,
        "sma_vol": 0.0,
        "last_vol": 0.0,
        "candle_dir": "FLAT",
        "proxy_delta_hint": "NO_DATA",
    }

  last = candles[-1]
  last_vol = float(last.get("volume") or 0.0)
  hist = candles[-(lookback + 1) : -1] if len(candles) > 1 else []
  vols = [float(c.get("volume") or 0.0) for c in hist if (c.get("volume") or 0.0) > 0]

  sma_vol = mean(vols) if vols else 0.0
  threshold_vol = sma_vol * multiplier
  above_threshold = (sma_vol > 0) and (last_vol >= threshold_vol)
  ratio = round(last_vol / sma_vol, 2) if sma_vol > 0 else 0.0

  if last["close"] > last["open"]:
    candle_dir = "BULLISH"
  elif last["close"] < last["open"]:
    candle_dir = "BEARISH"
  else:
    candle_dir = "FLAT"

  rng = max(last["high"] - last["low"], 1e-9)
  body = last["close"] - last["open"]
  body_strength = round(abs(body) / rng, 2)

  # პროქსი-მინიშნება: სანთლის მიმართულება და სხეულის სიმკვრივე
  if not above_threshold:
    proxy_hint = "BELOW_THRESHOLD"
  elif candle_dir == "BULLISH" and body_strength >= 0.45:
    proxy_hint = "STRONG_BULL_IMPULSE_VERIFY_TV_CIRCLE"
  elif candle_dir == "BEARISH" and body_strength >= 0.45:
    proxy_hint = "STRONG_BEAR_IMPULSE_VERIFY_TV_CIRCLE"
  else:
    proxy_hint = "MIXED_WICK_OR_WEAK_BODY_VERIFY_TV_CIRCLE"

  return {
      "above_threshold": above_threshold,
      "ratio": ratio,
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
    tol_ratio: float = 0.25,
) -> dict | None:
  """
    5M ეტაპი (მხოლოდ 30M + 3H კონტექსტის შემდეგ):
    1) Double Top / Double Bottom
    2) V-Reversal
    + Matrix v3.8 შეფასება 5M (200 / 1.60) და 30M (150 / 1.50) ტაიმფრეიმებზე.
    """
  if not aligned_3h or len(candles_5m) < 25:
    return None

  avg_rng = _avg_range(candles_5m, lookback=20)
  if avg_rng <= 0:
    return None

  tol = avg_rng * tol_ratio
  last = candles_5m[-1]
  last_body = abs(last["close"] - last["open"])
  last_range = max(last["high"] - last["low"], 1e-9)

  matrix_5m = evaluate_volume_matrix(candles_5m, lookback=200, multiplier=1.60)
  matrix_30m = evaluate_volume_matrix(candles_30m, lookback=150, multiplier=1.50)

  found: dict | None = None

  # 1. Double Top / Double Bottom
  window = candles_5m[-20:-1]
  if len(window) >= 10:
    highs = [(i, c["high"]) for i, c in enumerate(window)]
    h1_idx, h1_val = max(highs[: len(highs) // 2], key=lambda x: x[1])
    h2_idx, h2_val = max(highs[len(highs) // 2 :], key=lambda x: x[1])
    if (h2_idx - h1_idx) >= 5 and abs(h2_val - h1_val) <= tol:
      if last["close"] < last["open"] and last_range >= avg_rng * 0.8:
        found = {
            "pattern": "DOUBLE_TOP",
            "direction": "SHORT",
            "bias_30m": bias_30m,
            "level": round(mean([h1_val, h2_val]), 5),
            "last_candle": last,
            "impulse_ratio": round(last_body / last_range, 2),
        }

    if not found:
      lows = [(i, c["low"]) for i, c in enumerate(window)]
      l1_idx, l1_val = min(lows[: len(lows) // 2], key=lambda x: x[1])
      l2_idx, l2_val = min(lows[len(lows) // 2 :], key=lambda x: x[1])
      if (l2_idx - l1_idx) >= 5 and abs(l2_val - l1_val) <= tol:
        if last["close"] > last["open"] and last_range >= avg_rng * 0.8:
          found = {
              "pattern": "DOUBLE_BOTTOM",
              "direction": "LONG",
              "bias_30m": bias_30m,
              "level": round(mean([l1_val, l2_val]), 5),
              "last_candle": last,
              "impulse_ratio": round(last_body / last_range, 2),
          }

  # 2. V-Reversal
  if not found:
    prev_6 = candles_5m[-7:-1]
    move_6 = prev_6[-1]["close"] - prev_6[0]["open"]
    if abs(move_6) >= avg_rng * 2.2:
      if (
          move_6 < 0
          and last["close"] > last["open"]
          and last_range >= avg_rng * 1.1
      ):
        found = {
            "pattern": "V_REVERSAL",
            "direction": "LONG",
            "bias_30m": bias_30m,
            "level": round(min(c["low"] for c in prev_6), 5),
            "last_candle": last,
            "impulse_ratio": round(last_body / last_range, 2),
        }
      elif (
          move_6 > 0
          and last["close"] < last["open"]
          and last_range >= avg_rng * 1.1
      ):
        found = {
            "pattern": "V_REVERSAL",
            "direction": "SHORT",
            "bias_30m": bias_30m,
            "level": round(max(c["high"] for c in prev_6), 5),
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
