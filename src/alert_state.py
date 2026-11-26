import json
from datetime import datetime, timedelta
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


def build_cooldown_key(pair: str, direction: str) -> str:
  return f"COOL_{pair}_{direction}"


def is_in_cooldown(
    state: dict, cooldown_key: str, now: datetime, minutes: int = 120
) -> bool:
  """ერთსა და იმავე წყვილზე/მიმართულებაზე განმეორებითი სიგნალების შეჩერება."""
  raw = state.get(cooldown_key)
  if not raw:
    return False
  try:
    last = datetime.fromisoformat(raw)
  except ValueError:
    return False
  return timedelta(0) <= (now - last) < timedelta(minutes=minutes)


def mark_alert_sent(
    state: dict,
    alert_key: str,
    daily_key: str,
    iso_ts: str,
    cooldown_key: str | None = None,
) -> None:
  state[alert_key] = iso_ts
  state[daily_key] = iso_ts
  if cooldown_key:
    state[cooldown_key] = iso_ts


def prune_old_entries(state: dict, now: datetime, days: int = 3) -> tuple[dict, bool]:
  """შლის `days` დღეზე ძველ ჩანაწერებს, რომ state ფაილი არ გაიზარდოს."""
  cutoff = now - timedelta(days=days)
  cleaned = {}
  for k, v in state.items():
    try:
      ts = datetime.fromisoformat(v)
      if ts < cutoff:
        continue
    except (TypeError, ValueError):
      pass
    cleaned[k] = v
  return cleaned, (len(cleaned) != len(state))
