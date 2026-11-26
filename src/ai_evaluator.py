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
შენ ხარ დავით ბიტაძის სავაჭრო სისტემის ობიექტური, მშვიდი და მკაცრად ანალიტიკური AI-ასისტენტი.
მთავარი პრინციპი: ხარისხი ყოველთვის დგას რაოდენობასა და ხმაურზე მაღლა. საბოლოო გადაწყვეტილებას იღებს ტრეიდერი.

ენის წესი:
- setup_summary და risk_and_caution ყოველთვის დაწერე მხოლოდ გამართულ, ბუნებრივ ქართულ ენაზე (ინგლისური წინადადებების გარეშე; ტექნიკური აბრევიატურები დასაშვებია).

სისტემური პრიორიტეტები:
- 30M განსაზღვრავს ფაზას/ტრენდს; 3H ადასტურებს ხედვას.
- 5M არის სამუშაო ტაიმფრეიმი (Double Top / Double Bottom).
- სტრუქტურული არკის წესი: მინიმუმ 14 სანთელი, არკის გადახრა საწყისი სანთლის მასშტაბთან ~2.5x, დონის ღრმა ჩახვრეტის გარეშე.
- სავაჭრო ფანჯარა (თბილისი): 13:00 - 21:30; დახურვის ზღვარი: 00:30 (ზაფხული) / 01:30 (ზამთარი).

შეფასების სტილი:
- იყავი ობიექტური და გაწონასწორებული: მოკლედ აღწერე რა ადასტურებს სეტაპს და რა ასუსტებს მას (მაგ. მოცულობის მატრიცა, 30M RANGE/ტრენდი, არკის სისუფთავე).
- notify=true მიეცი მხოლოდ მაშინ, როცა სეტაპი ლოგიკურად გამართულია და ღირს ტრეიდერის ყურადღებად (სასურველი quality_score >= 7). თუ სტრუქტურა ბუნდოვანია ან „ხმაურს“ ჰგავს, მიეცი notify=false და დაბალი ქულა.
"""


def _is_daily_quota_error(err_str: str) -> bool:
  return (
      "RESOURCE_EXHAUSTED" in err_str
      or "PerDay" in err_str
      or "per day limit exceeded" in err_str.lower()
      or "429" in err_str
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

  primary_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
  fallback_model = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
  models_to_try = [primary_model]
  if fallback_model and fallback_model != primary_model:
    models_to_try.append(fallback_model)

  client = genai.Client(api_key=api_key)
  prompt = (
      f"დრო (თბილისი): {tbilisi_now_str}\n"
      f"წყვილი: {pair}\n"
      f"კანდიდატის მონაცემები: {json.dumps(candidate, ensure_ascii=False)}\n"
      "შეაფასე სეტაპი ობიექტურად და დააბრუნე სტრუქტურირებული პასუხი. "
      "setup_summary და risk_and_caution აუცილებლად ქართულად დაწერე."
  )

  last_exc = None
  for model_name in models_to_try:
    max_retries = 2
    for attempt in range(max_retries + 1):
      try:
        response = _generate(client, model_name, prompt)
        if not response.text:
          raise ValueError(f"მოდელმა ({model_name}) ცარიელი პასუხი დააბრუნა.")
        return json.loads(response.text)
      except Exception as exc:
        last_exc = exc
        err_str = str(exc)
        if _is_daily_quota_error(err_str):
          break
        if _is_transient_503(err_str) and attempt < max_retries:
          time.sleep(10 * (attempt + 1))
          continue
        break

  raise last_exc


def _generate(client, model_name: str, prompt: str):
  return client.models.generate_content(
      model=model_name,
      contents=prompt,
      config=types.GenerateContentConfig(
          system_instruction=SYSTEM_PROMPT,
          response_mime_type="application/json",
          response_schema=SetupEvaluation,
          temperature=0.2,
      ),
  )
