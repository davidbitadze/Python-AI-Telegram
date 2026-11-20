import os
import urllib.parse
import urllib.request
import json


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
  with urllib.request.urlopen(url, timeout=20) as resp:
    data = json.loads(resp.read().decode("utf-8"))

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
