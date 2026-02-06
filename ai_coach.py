import os
import json
import requests
from typing import Optional

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Model can be overridden from Render env var OPENROUTER_MODEL
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct")

# Use your deployed URL as referer (Render provides RENDER_EXTERNAL_URL sometimes; fallback is fine)
APP_URL = os.environ.get("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "https://bmi-app-qt7b.onrender.com"


def _get_openrouter_key() -> Optional[str]:
    # IMPORTANT: only use OPENROUTER_API_KEY (no OPENAI_API_KEY fallback)
    return os.environ.get("OPENROUTER_API_KEY")


def _call_openrouter(messages, temperature: float = 0.7, max_tokens: int = 900) -> str:
    api_key = _get_openrouter_key()

    if not api_key:
        return "AI Coach is unavailable: OPENROUTER_API_KEY is not set on the server."

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",

        # OpenRouter recommends these (ok for prod too)
        "HTTP-Referer": APP_URL,
        "X-Title": "BMI App AI Coach",
    }

    payload = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=45)

        if r.status_code != 200:
            # Print to Render logs so you can debug quickly
            print("OPENROUTER_ERROR_STATUS:", r.status_code)
            print("OPENROUTER_ERROR_TEXT:", r.text)

            try:
                err = r.json()
                return (
                    f"AI Coach is unavailable (HTTP {r.status_code}).\n\n"
                    f"Details:\n{json.dumps(err, indent=2)}"
                )
            except Exception:
                return f"AI Coach is unavailable (HTTP {r.status_code}).\n\nDetails:\n{r.text}"

        data = r.json()

        if "choices" in data and data["choices"]:
            choice0 = data["choices"][0]
            if "message" in choice0 and "content" in choice0["message"]:
                return str(choice0["message"]["content"]).strip()
            if "text" in choice0:
                return str(choice0["text"]).strip()

        return "AI Coach error: Unexpected response format.\n\nRaw:\n" + json.dumps(data, indent=2)

    except requests.exceptions.Timeout:
        return "AI Coach timed out. Please try again."
    except requests.exceptions.RequestException as e:
        return f"AI Coach network error: {str(e)}"
    except Exception as e:
        return f"AI Coach error: {str(e)}"


def generate_bmi_coach_plan(name: str, bmi: float, goal: str) -> str:
    prompt = f"""
Create a structured 4-week fitness plan for {name}.
User goal: {goal}
User BMI: {bmi}

IMPORTANT FORMAT RULES (must follow EXACTLY):
- Use headings like 'WEEK 1:', 'WEEK 2:', 'WEEK 3:', 'WEEK 4:' (all caps, with colon).
- Each week heading must be on its own line, with no text before it on that line.
- Do NOT add any other headings before WEEK 1.
- Do NOT reorder the weeks.

Output MUST be EXACTLY in this structure:

WEEK 1:
- bullet points
Motivation: "short quote"

WEEK 2:
- bullet points
Motivation: "short quote"

WEEK 3:
- bullet points
Motivation: "short quote"

WEEK 4:
- bullet points
Motivation: "short quote"

Content rules:
- Keep it beginner-friendly and safe.
- Include both diet and exercise ideas each week.
- Include simple weekly target habits (like steps, sleep, water).
- Avoid extreme dieting or unsafe advice.
"""

    messages = [
        {"role": "system", "content": "You are a helpful fitness coach. Be safe, realistic, and beginner-friendly."},
        {"role": "user", "content": prompt},
    ]

    return _call_openrouter(messages=messages, temperature=0.7, max_tokens=950)


def ask_ai_coach(question: str, name: str, bmi: float, goal: str, week: int) -> str:
    prompt = f"""
You are an AI fitness coach inside a BMI app.

User: {name}
BMI: {bmi}
Goal: {goal}
Current Week in Plan: Week {week}

User question: {question}

Answer clearly in short steps:
- Give safe beginner advice.
- If the user asks about workout: include sets/reps or duration.
- If the user asks about food: give simple meal suggestions.
- Add exactly 1 short motivational line at the end.
"""

    messages = [
        {"role": "system", "content": "You are a helpful fitness coach. Be safe, realistic, and beginner-friendly."},
        {"role": "user", "content": prompt},
    ]

    return _call_openrouter(messages=messages, temperature=0.6, max_tokens=500)
