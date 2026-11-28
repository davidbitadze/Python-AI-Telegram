from datetime import datetime, time
from zoneinfo import ZoneInfo

TBILISI_TZ = ZoneInfo("Asia/Tbilisi")

PAIRS = [
    "USDJPY",
    "EURAUD",
    "GBPAUD",
    "USDCAD",
    "EURUSD",
    "NZDUSD",
    "GBPJPY",
    "EURJPY",
    "AUDUSD",
    "GBPUSD",
]

ENTRY_WINDOW_START = time(12, 30)
ENTRY_WINDOW_END = time(21, 30)

# პრიორიტეტული პატერნები: 1) Double Top / Double Bottom, 2) V-Reversal
PRIORITY_PATTERNS = ["DOUBLE_TOP_BOTTOM", "V_REVERSAL"]


def is_within_entry_window(now_tbilisi: datetime | None = None) -> bool:
  now = now_tbilisi or datetime.now(TBILISI_TZ)
  # შაბათ-კვირას ფორექსი დაკეტილია
  if now.weekday() >= 5:
    return False
  current_time = now.time()
  return ENTRY_WINDOW_START <= current_time <= ENTRY_WINDOW_END
