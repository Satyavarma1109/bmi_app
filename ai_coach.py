import os
import json
import requests
from typing import Optional

# OpenRouter endpoint
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default model (you can change later)
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct")

# (Optional) Set this in Render as your deployed URL, e.g.:
# APP_URL=https://bmi-app.onrender.com
APP_URL = os.environ.get("APP_URL", "http://localhost:5000")


def _get_openrouter_key() -> Optional[str]:
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    print("OPENROUTER_API_KEY present:", bool(key))
    return key


def _call_openrouter(messages, temperature: float = 0.7, max_tokens: int = 900) -> str:
    """
    Low-level OpenRouter call. Always returns STRING.
    """
    api_key = _get_openrouter_key()

    # Safe debug (does NOT print the key)
    print("OPENROUTER_API_KEY present:", bool(api_key), "| MODEL:", DEFAULT_MODEL, "| APP_URL:", APP_URL)

    if not api_key:
        return "AI Coach is unavailable: OPENROUTER_API_KEY is not set in Render Environment Variables."

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",

        # Recommended by OpenRouter (helps tracking)
        # Use your deployed URL on Render (not localhost)
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
            try:
                err = r.json()
                return f"AI Coach is unavailable (HTTP {r.status_code}).\n\nDetails:\n{json.dumps(err, indent=2)}"
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
    """
    Returns a 4-week plan as plain text (string).
    The output is strictly structured so we can reliably split it into weeks.
    """

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
    """
    This is for the live chat agent inside coach page.
    Always returns a string answer.
    """
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
