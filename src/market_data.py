import os
import urllib.parse
import urllib.request
import json


class RateLimitError(Exception):
  """Twelve Data აბრუნებს HTTP 200-ს და JSON-ში code=429-ს (წუთობრივი/დღიური ლიმიტი)."""

  def __init__(self, message: str, daily: bool = False):
    super().__init__(message)
    self.daily = daily


def format_symbol(pair: str) -> str:
  # "EURUSD" -> "EUR/USD"
  return f"{pair[:3]}/{pair[3:]}"


def fetch_candles(pair: str, interval: str, outputsize: int = 120) -> list[dict]:
  api_key = os.environ["MARKET_API_KEY"]
  symbol = urllib.parse.quote(format_symbol(pair))
  url = (
      f"https://api.twelvedata.com/time_series?symbol={symbol}"
      f"&interval={interval}&outputsize={outputsize}&apikey={api_key}"
  )
  # დრო თბილისის ზონაში (ნაგულისხმევად ბირჟის ზონაა, ~+7სთ თბილისიდან)
  def _get(u: str) -> dict:
    with urllib.request.urlopen(u, timeout=20) as resp:
      return json.loads(resp.read().decode("utf-8"))

  data = _get(url + "&timezone=Asia%2FTbilisi")
  if isinstance(data, dict) and data.get("code") == 429:
    msg = str(data.get("message", data))
    raise RateLimitError(
        f"Twelve Data rate limit ({pair} {interval}): {msg}",
        daily="day" in msg.lower(),
    )

  if "values" not in data and "timezone" in str(data).lower():
    data = _get(url)  # fallback: თუ timezone პარამეტრი არ მიიღეს

  if "values" not in data:
    raise RuntimeError(f"Failed fetching {pair} ({interval}): {data}")

  # Twelve Data აბრუნებს უახლესიდან ძველისკენ -> ვაბრუნებთ ქრონოლოგიურად
  candles = list(reversed(data["values"]))
  return [
      {
          "datetime": c["datetime"],
          "open": float(c["open"]),
          "high": float(c["high"]),
          "low": float(c["low"]),
          "close": float(c["close"]),
          "volume": float(c.get("volume") or 0.0),
      }
      for c in candles
  ]
