import json
import os
import time
from google import genai
from google.genai import types
from pydantic import BaseModel


class SetupEvaluation(BaseModel):
  notify: bool
  quality_score: int
  setup_summary: str
  risk_and_caution: str


SYSTEM_PROMPT = """
შენ ხარ დავით ბიტაძის სავაჭრო სისტემის ობიექტური AI-ასისტენტი და მკაცრი აუდიტორი.
პრიორიტეტები:
- 30M ტაიმფრეიმი განსაზღვრავს ტრენდს/ფაზას; 3H ადასტურებს ხედვას.
- 5M არის სამუშაო ტაიმფრეიმი (პატერნი, იმპულსი/ტრიგერი).
- აქტიური პატერნებია მხოლოდ Double Top / Double Bottom.
- ვაჭრობის სავაჭრო დროის ფანჯარა (საქართველოს დროით): 13:00 - 21:30; დახურვის საბოლოო ზღვარი: 00:30 (ზაფხული) / 01:30 (ზამთარი).
- იყავი პირდაპირი, ობიექტური და კონკრეტული, ზედმეტი ემოციების გარეშე.
- notify=true მიეცი მხოლოდ მაშინ, როცა სეტაპი ლოგიკურად შეესაბამება 30M/3H კონტექსტს და 5M პატერნს (სასურველი ხარისხი >= 7/10).
"""


def _is_daily_quota_error(err_str: str) -> bool:
  return (
      "RESOURCE_EXHAUSTED" in err_str
      or "PerDay" in err_str
      or "per day limit exceeded" in err_str.lower()
  )


def _is_transient_503(err_str: str) -> bool:
  return (
      "503" in err_str
      or "UNAVAILABLE" in err_str
      or "ServiceUnavailable" in err_str
      or "high demand" in err_str.lower()
  )


def evaluate_candidate(pair: str, candidate: dict, tbilisi_now_str: str) -> dict:
  api_key = os.environ.get("GEMINI_API_KEY")
  if not api_key:
    raise RuntimeError("GEMINI_API_KEY არ არის დაყენებული გარემოში.")

  client = genai.Client(api_key=api_key)
  prompt = (
      f"დრო (თბილისი): {tbilisi_now_str}\n"
      f"წყვილი: {pair}\n"
      f"კანდიდატის მონაცემები: {json.dumps(candidate, ensure_ascii=False)}\n"
      "შეაფასე სეტაპი წესების მიხედვით და დააბრუნე სტრუქტურირებული პასუხი."
  )

  last_exc = None
  max_retries = 2
  for attempt in range(max_retries + 1):
    try:
      response = _generate(client, prompt)
      if not response.text:
        raise ValueError("Gemini-მ ცარიელი პასუხი დააბრუნა.")
      return json.loads(response.text)
    except Exception as exc:
      last_exc = exc
      err_str = str(exc)
      if _is_daily_quota_error(err_str):
        raise
      if _is_transient_503(err_str) and attempt < max_retries:
        time.sleep(15 * (attempt + 1))
        continue
      raise
  raise last_exc


def _generate(client, prompt):
  return client.models.generate_content(
      model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
      contents=prompt,
      config=types.GenerateContentConfig(
          system_instruction=SYSTEM_PROMPT,
          response_mime_type="application/json",
          response_schema=SetupEvaluation,
          temperature=0.2,
      ),
  )
