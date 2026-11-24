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


def evaluate_candidate(pair: str, candidate: dict, tbilisi_now_str: str) -> dict:
  api_key = os.environ.get("GEMINI_API_KEY")
  if not api_key:
    return {
        "notify": True,
        "quality_score": 7,
        "setup_summary": f"{candidate['pattern']} ({candidate['direction']}) on level {candidate['level']} [30M: {candidate['bias_30m']}]",
        "risk_and_caution": "GEMINI_API_KEY არ არის დაყენებული; გაიგზავნა ტექნიკური ფილტრის კანდიდატი.",
    }

  client = genai.Client(api_key=api_key)
  prompt = (
      f"დრო (თბილისი): {tbilisi_now_str}\n"
      f"წყვილი: {pair}\n"
      f"კანდიდატის მონაცემები: {json.dumps(candidate, ensure_ascii=False)}\n"
      "შეაფასე სეტაპი წესების მიხედვით და დააბრუნე სტრუქტურირებული პასუხი."
  )

  last_exc = None
  for attempt in range(3):
    try:
      response = _generate(client, prompt)
      return json.loads(response.text)
    except Exception as exc:  # 503/429 დროებითი გადატვირთვა -> ვცდით თავიდან
      last_exc = exc
      if attempt < 2 and any(c in str(exc) for c in ("503", "429", "UNAVAILABLE")):
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
