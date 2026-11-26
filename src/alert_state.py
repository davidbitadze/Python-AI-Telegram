import json
from pathlib import Path

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


def prune_old_daily_keys(state: dict, today_str: str) -> tuple[dict, bool]:
  """ასუფთავებს წინა დღეების `DAILY_...` ჩანაწერებს `state/sent_alerts.json`-ში."""
  today_prefix = f"DAILY_{today_str}_"
  cleaned = {
      k: v
      for k, v in state.items()
      if not k.startswith("DAILY_") or k.startswith(today_prefix)
  }
  return cleaned, (len(cleaned) != len(state))


def build_alert_key(pair: str, pattern: str, direction: str, candle_dt: str) -> str:
  return f"{pair}_{pattern}_{direction}_{candle_dt}"


def build_daily_level_key(
    today_str: str, pair: str, pattern: str, direction: str, level: float
) -> str:
  lvl_rounded = round(float(level), 4)
  return f"DAILY_{today_str}_{pair}_{pattern}_{direction}_{lvl_rounded}"


def is_duplicate_alert(state: dict, alert_key: str, daily_key: str) -> bool:
  return bool(state.get(alert_key) or state.get(daily_key))


def mark_alert_sent(state: dict, alert_key: str, daily_key: str, iso_ts: str) -> None:
  state[alert_key] = iso_ts
  state[daily_key] = iso_ts
