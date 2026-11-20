from statistics import mean


def _avg_range(candles: list[dict], lookback: int = 20) -> float:
  if not candles:
    return 0.0
  subset = candles[-lookback:]
  ranges = [max(0.0, c["high"] - c["low"]) for c in subset]
  return mean(ranges) if ranges else 0.0


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

  if high_2 > high_1 and low_2 >= low_1 and last_close > mean(c["close"] for c in first_half):
    return "BULLISH"
  if low_2 < low_1 and high_2 <= high_1 and last_close < mean(c["close"] for c in first_half):
    return "BEARISH"
  return "RANGE"


def confirm_3h_alignment(candles_3h: list[dict], bias_30m: str) -> bool:
  """3H ადასტურებს 30M-ის ხედვას (ან RANGE-ის შემთხვევაში იძლევა კონტექსტს)."""
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


def detect_5m_candidate(
    candles_5m: list[dict],
    bias_30m: str,
    aligned_3h: bool,
    tol_ratio: float = 0.25,
) -> dict | None:
  """
    პრიორიტეტი: Double Top / Double Bottom; შემდგომ: V-Reversal.
    აბრუნებს კანდიდატ სეტაპს AI-სთან გადასამოწმებლად.
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

  # 1. Double Top / Double Bottom (ბოლო ~20 სანთლის ფანჯარაში)
  window = candles_5m[-20:-1]
  if len(window) >= 10:
    # Double Top (შებრუნება დაბლა / Short კანდიდატი)
    highs = [(i, c["high"]) for i, c in enumerate(window)]
    h1_idx, h1_val = max(highs[: len(highs) // 2], key=lambda x: x[1])
    h2_idx, h2_val = max(highs[len(highs) // 2 :], key=lambda x: x[1])
    if (h2_idx - h1_idx) >= 5 and abs(h2_val - h1_val) <= tol:
      if last["close"] < last["open"] and last_range >= avg_rng * 0.8:
        return {
            "pattern": "DOUBLE_TOP",
            "direction": "SHORT",
            "bias_30m": bias_30m,
            "level": round(mean([h1_val, h2_val]), 5),
            "last_candle": last,
            "impulse_ratio": round(last_body / last_range, 2),
        }

    # Double Bottom (შებრუნება მაღლა / Long კანდიდატი)
    lows = [(i, c["low"]) for i, c in enumerate(window)]
    l1_idx, l1_val = min(lows[: len(lows) // 2], key=lambda x: x[1])
    l2_idx, l2_val = min(lows[len(lows) // 2 :], key=lambda x: x[1])
    if (l2_idx - l1_idx) >= 5 and abs(l2_val - l1_val) <= tol:
      if last["close"] > last["open"] and last_range >= avg_rng * 0.8:
        return {
            "pattern": "DOUBLE_BOTTOM",
            "direction": "LONG",
            "bias_30m": bias_30m,
            "level": round(mean([l1_val, l2_val]), 5),
            "last_candle": last,
            "impulse_ratio": round(last_body / last_range, 2),
        }

  # 2. V-Reversal (მკვეთრი საპირისპირო იმპულსი ბოლო სანთელზე)
  prev_6 = candles_5m[-7:-1]
  move_6 = prev_6[-1]["close"] - prev_6[0]["open"]
  if abs(move_6) >= avg_rng * 2.2:
    # წინა 6 სანთელი მკვეთრად ეცემოდა -> ახლა მკვეთრი ზრდა
    if move_6 < 0 and last["close"] > last["open"] and last_range >= avg_rng * 1.1:
      return {
          "pattern": "V_REVERSAL",
          "direction": "LONG",
          "bias_30m": bias_30m,
          "level": round(min(c["low"] for c in prev_6), 5),
          "last_candle": last,
          "impulse_ratio": round(last_body / last_range, 2),
      }
    # წინა 6 სანთელი მკვეთრად იზრდებოდა -> ახლა მკვეთრი კლება
    if move_6 > 0 and last["close"] < last["open"] and last_range >= avg_rng * 1.1:
      return {
          "pattern": "V_REVERSAL",
          "direction": "SHORT",
          "bias_30m": bias_30m,
          "level": round(max(c["high"] for c in prev_6), 5),
          "last_candle": last,
          "impulse_ratio": round(last_body / last_range, 2),
      }

  return None
