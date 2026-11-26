from datetime import date, datetime, timedelta


def _easter_sunday(year: int) -> date:
  """Anonymous Gregorian algorithm — აღდგომის კვირა ნებისმიერი წლისთვის."""
  a = year % 19
  b = year // 100
  c = year % 100
  d = b // 4
  e = b % 4
  f = (b + 8) // 25
  g = (b - f + 1) // 3
  h = (19 * a + b - d - g + 15) % 30
  i = c // 4
  k = c % 4
  l = (32 + 2 * e + 2 * i - h - k) % 7
  m = (a + 11 * h + 22 * l) // 451
  month = (h + l - 7 * m + 114) // 31
  day = ((h + l - 7 * m + 114) % 31) + 1
  return date(year, month, day)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
  """აბრუნებს თვის n-ურ სამუშაო დღეს (weekday: 0=Mon .. 6=Sun)."""
  first_day = date(year, month, 1)
  offset = (weekday - first_day.weekday()) % 7
  return first_day + timedelta(days=offset + (n - 1) * 7)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
  """აბრუნებს თვის ბოლო კონკრეტულ კვირის დღეს."""
  if month == 12:
    next_month_first = date(year + 1, 1, 1)
  else:
    next_month_first = date(year, month + 1, 1)
  last_day = next_month_first - timedelta(days=1)
  offset = (last_day.weekday() - weekday) % 7
  return last_day - timedelta(days=offset)


def _observed_us_holiday(d: date) -> set[date]:
  dates = {d}
  if d.weekday() == 5:
    dates.add(d - timedelta(days=1))
  elif d.weekday() == 6:
    dates.add(d + timedelta(days=1))
  return dates


def get_us_nyse_holidays(year: int) -> set[date]:
  """ნიუ-იორკის ბირჟის (NYSE) დასვენების დღეები."""
  holidays: set[date] = set()
  for base in (
      date(year, 1, 1),
      date(year, 6, 19),
      date(year, 7, 4),
      date(year, 12, 25),
  ):
    holidays |= _observed_us_holiday(base)

  holidays.add(_nth_weekday_of_month(year, 1, 0, 3))     # MLK Day
  holidays.add(_nth_weekday_of_month(year, 2, 0, 3))     # Presidents' Day
  holidays.add(_easter_sunday(year) - timedelta(days=2))  # Good Friday
  holidays.add(_last_weekday_of_month(year, 5, 0))      # Memorial Day
  holidays.add(_nth_weekday_of_month(year, 9, 0, 1))     # Labor Day
  holidays.add(_nth_weekday_of_month(year, 11, 3, 4))   # Thanksgiving
  return holidays


def get_uk_lse_holidays(year: int) -> set[date]:
  """ლონდონის ბირჟის (LSE) დასვენების დღეები."""
  holidays: set[date] = set()

  ny = date(year, 1, 1)
  if ny.weekday() == 5:
    holidays.add(date(year, 1, 3))
  elif ny.weekday() == 6:
    holidays.add(date(year, 1, 2))
  else:
    holidays.add(ny)

  holidays.add(_easter_sunday(year) - timedelta(days=2))  # Good Friday
  holidays.add(_easter_sunday(year) + timedelta(days=1))  # Easter Monday
  holidays.add(_nth_weekday_of_month(year, 5, 0, 1))     # Early May Bank Holiday
  holidays.add(_last_weekday_of_month(year, 5, 0))      # Spring Bank Holiday
  holidays.add(_last_weekday_of_month(year, 8, 0))      # Summer Bank Holiday

  xmas = date(year, 12, 25)
  boxing = date(year, 12, 26)
  if xmas.weekday() == 5:
    holidays.update({date(year, 12, 27), date(year, 12, 28)})
  elif xmas.weekday() == 6:
    holidays.update({date(year, 12, 26), date(year, 12, 27)})
  elif boxing.weekday() == 6:
    holidays.update({xmas, date(year, 12, 28)})
  else:
    holidays.update({xmas, boxing})

  return holidays


def check_market_holidays(d: date) -> tuple[bool, bool, str]:
  """აბრუნებს `(uk_closed, us_closed, status_label)`."""
  uk_closed = d in get_uk_lse_holidays(d.year)
  us_closed = d in get_us_nyse_holidays(d.year)
  if uk_closed and us_closed:
    return True, True, "BOTH_UK_US_HOLIDAY"
  if uk_closed:
    return True, False, "UK_LSE_HOLIDAY"
  if us_closed:
    return False, True, "US_NYSE_HOLIDAY"
  return False, False, "OPEN"


def is_weekend(dt: datetime) -> bool:
  """შაბათი (5) ან კვირა (6)."""
  return dt.weekday() >= 5
