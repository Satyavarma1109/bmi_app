import os
import requests
import json

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

def generate_bmi_coach_plan(username: str, bmi: float, goal: str) -> dict:
    if not OPENROUTER_API_KEY:
        return {"error": "AI Coach is not configured (missing API key)."}

    prompt = f"""
Return ONLY valid JSON (no markdown, no extra text).

Create a 4-week fitness plan for:
username: {username}
bmi: {bmi}
goal: {goal}

JSON format MUST be exactly:
{{
  "overview": "1-2 lines",
  "weeks": [
    {{
      "week": 1,
      "title": "short title",
      "goals": ["...", "...", "..."],
      "diet": ["...", "...", "..."],
      "workout": ["...", "...", "..."],
      "quote": "short motivational quote"
    }},
    {{
      "week": 2,
      "title": "...",
      "goals": ["..."],
      "diet": ["..."],
      "workout": ["..."],
      "quote": "..."
    }},
    {{
      "week": 3,
      "title": "...",
      "goals": ["..."],
      "diet": ["..."],
      "workout": ["..."],
      "quote": "..."
    }},
    {{
      "week": 4,
      "title": "...",
      "goals": ["..."],
      "diet": ["..."],
      "workout": ["..."],
      "quote": "..."
    }}
  ]
}}
"""

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-Title": "BMI AI Coach App"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4
            },
            timeout=30
        )

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # Parse JSON safely
        plan = json.loads(content)
        return plan

    except Exception as e:
        return {"error": f"AI Coach error: {str(e)}"}
