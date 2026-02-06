from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import re
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import secrets
import smtplib
import os
from email.message import EmailMessage

from ai_coach import generate_bmi_coach_plan, ask_ai_coach


# ---------------- CONFIG ----------------
APP_SECRET = os.environ.get("APP_SECRET", "change-me-to-a-random-string")
DB_FILE = os.environ.get("DB_FILE", "bmi.db")

# Email configuration
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "465"))
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")
USE_SSL = True  # True for 465, False for 587

TOKEN_EXPIRATION_MINUTES = 30  # reset link validity

app = Flask(__name__)
app.secret_key = APP_SECRET


# ---------------- DB Helpers ----------------
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bmi_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        weight REAL NOT NULL,
        height REAL NOT NULL,
        bmi REAL NOT NULL,
        category TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_resets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS coach_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        goal TEXT NOT NULL,
        plan_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    conn.commit()
    conn.close()


init_db()


# ---------------- Password validation ----------------
def is_valid_password(password):
    if len(password) < 6:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    return True


# IMPORTANT FIX: Avoid scrypt error on some Windows builds
def hash_password(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)


# ---------------- Email helper ----------------
def send_email(to_email, subject, body):
    if not EMAIL_HOST or not EMAIL_USER or not EMAIL_PASS:
        print("=== EMAIL NOT SENT (SMTP not configured) ===")
        print("To:", to_email)
        print("Subject:", subject)
        print(body)
        print("==========================================")
        return

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = to_email
        msg.set_content(body)

        if USE_SSL:
            with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT) as server:
                server.login(EMAIL_USER, EMAIL_PASS)
                server.send_message(msg)
        else:
            with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(EMAIL_USER, EMAIL_PASS)
                server.send_message(msg)
    except Exception as e:
        print("Failed to send email:", e)
        print("Fallback to console output:")
        print(body)


# ---------------- AI helpers ----------------
def split_plan_into_weeks(plan_text: str) -> dict:
    """
    Takes AI plan text and returns dict: {1: "...", 2: "...", 3: "...", 4: "..."}.
    Much more tolerant about the exact heading format.
    """
    import re

    if not isinstance(plan_text, str):
        plan_text = str(plan_text)

    text = plan_text.replace("\r\n", "\n")
    pattern = re.compile(r"(week\s*([1-4])\s*[:\-]?)", re.IGNORECASE)
    matches = list(pattern.finditer(text))

    weeks = {1: "", 2: "", 3: "", 4: ""}

    if not matches:
        weeks[1] = text.strip()
        return weeks

    for i, match in enumerate(matches):
        week_num_str = match.group(2)
        try:
            week_num = int(week_num_str)
        except (TypeError, ValueError):
            continue

        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        section = text[start_idx:end_idx].strip()
        if section:
            weeks[week_num] = section

    if not any(weeks.values()):
        weeks[1] = text.strip()

    return weeks


def get_latest_bmi(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT bmi FROM bmi_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row["bmi"] if row else None


def get_latest_bmi_row(user_id: int):
    """Returns latest BMI row (weight/height/bmi/category/timestamp) or None."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM bmi_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def compute_category(bmi: float) -> str:
    if bmi < 18.5:
        return "Underweight"
    elif bmi < 25:
        return "Normal"
    elif bmi < 30:
        return "Overweight"
    return "Obese"


# ---------------- Routes ----------------
@app.route("/")
def root():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


# ---------- REGISTER ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if not is_valid_password(password):
            flash("Password must be at least 6 chars and include upper, lower, and number.", "error")
            return render_template("register.html")

        password_hash = hash_password(password)

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                (username, email, password_hash)
            )
            conn.commit()

            cur.execute("SELECT id FROM users WHERE username = ?", (username,))
            user = cur.fetchone()
            session["user_id"] = user["id"]
            session["username"] = username
            flash("Account created and logged in.", "success")
            return redirect(url_for("dashboard"))
        except sqlite3.IntegrityError as e:
            if "username" in str(e).lower():
                flash("Username not available.", "error")
            elif "email" in str(e).lower():
                flash("Email already exists.", "error")
            else:
                flash("Username or email already exists.", "error")
        finally:
            conn.close()
    return render_template("register.html")


# ---------- LOGIN ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
        user = cur.fetchone()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["username"] = user["username"]

        session.pop("coach_plan_text", None)
        session.pop("coach_weeks", None)
        session.pop("coach_goal", None)
        session.pop("coach_chat", None)

        return redirect(url_for("dashboard"))

    return render_template("login.html")


# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


# ---------- FORGOT PASSWORD ----------
@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, username FROM users WHERE email = ?", (email,))
        user = cur.fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRATION_MINUTES)).isoformat()
            cur.execute(
                "INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, ?)",
                (user["id"], token, expires_at)
            )
            conn.commit()

            reset_url = url_for("reset_password", token=token, _external=True)
            email_body = (
                f"Hi {user['username']},\n\n"
                f"Reset your password (valid {TOKEN_EXPIRATION_MINUTES} min):\n{reset_url}"
            )
            send_email(email, "Password Reset", email_body)
        conn.close()
        flash("If that email exists in our system, a reset link has been sent.", "info")
        return redirect(url_for("login"))

    return render_template("forgot.html")


# ---------- RESET PASSWORD ----------
@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, expires_at, used FROM password_resets WHERE token = ?", (token,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("login"))

    expires_at = datetime.fromisoformat(row["expires_at"])
    if row["used"] or datetime.utcnow() > expires_at:
        conn.close()
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        new_pw = request.form["password"]
        if not is_valid_password(new_pw):
            flash("Password must be at least 6 chars and include upper, lower, and number.", "error")
            conn.close()
            return render_template("reset.html")

        pw_hash = hash_password(new_pw)
        cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, row["user_id"]))
        cur.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        flash("Password reset successfully. You can now login.", "success")
        return redirect(url_for("login"))

    conn.close()
    return render_template("reset.html")


# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("dashboard.html", username=session["username"])


# ---------- BMI CALCULATOR ----------
@app.route("/bmi", methods=["GET", "POST"])
def bmi_page():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    error = None
    bmi_result = None
    category = None
    yesterday_entry = None

    normal_min_weight = None
    normal_max_weight = None
    weight_to_lose = None
    weight_to_gain = None

    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        try:
            weight = float(request.form["weight"])
            height = float(request.form["height"])

            if weight <= 0 or height <= 0:
                raise ValueError("Weight and height must be positive numbers.")

            bmi = round(weight / (height ** 2), 2)
            cat = compute_category(bmi)

            normal_min_weight = round(18.5 * (height ** 2), 1)
            normal_max_weight = round(24.9 * (height ** 2), 1)

            if bmi >= 25:
                weight_to_lose = round(weight - normal_max_weight, 1)
                if weight_to_lose < 0:
                    weight_to_lose = 0.0
            elif bmi < 18.5:
                weight_to_gain = round(normal_min_weight - weight, 1)
                if weight_to_gain < 0:
                    weight_to_gain = 0.0

            timestamp = datetime.utcnow().isoformat()
            cur.execute(
                "INSERT INTO bmi_history (user_id, weight, height, bmi, category, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, weight, height, bmi, cat, timestamp)
            )
            conn.commit()

            bmi_result = bmi
            category = cat

        except Exception as e:
            error = "Invalid input. Make sure weight/height are valid numbers."
            print("BMI ERROR:", e)

    # Always fetch history (for chart + yesterday)
    cur.execute(
        "SELECT * FROM bmi_history WHERE user_id = ? ORDER BY timestamp DESC",
        (user_id,)
    )
    rows = cur.fetchall()

    # If user just opened page (GET) OR POST didn't compute, show latest saved BMI
    if bmi_result is None and rows:
        latest = rows[0]
        bmi_result = latest["bmi"]
        category = latest["category"]

        height = latest["height"]
        weight = latest["weight"]

        normal_min_weight = round(18.5 * (height ** 2), 1)
        normal_max_weight = round(24.9 * (height ** 2), 1)

        if bmi_result >= 25:
            weight_to_lose = round(weight - normal_max_weight, 1)
            if weight_to_lose < 0:
                weight_to_lose = 0.0
        elif bmi_result < 18.5:
            weight_to_gain = round(normal_min_weight - weight, 1)
            if weight_to_gain < 0:
                weight_to_gain = 0.0

    # Yesterday entry
    if rows:
        today = datetime.utcnow().date()
        for r in rows:
            entry_date = datetime.fromisoformat(r["timestamp"]).date()
            if entry_date < today:
                yesterday_entry = r
                break

    conn.close()

    # Chart data (oldest -> newest looks better)
    chart_dates = []
    chart_bmis = []
    for r in reversed(rows):
        try:
            dt = datetime.fromisoformat(r["timestamp"])
            chart_dates.append(dt.strftime("%b %d"))
        except Exception:
            chart_dates.append(r["timestamp"][:10])
        chart_bmis.append(r["bmi"])

    return render_template(
        "bmi.html",
        username=session["username"],
        error=error,
        bmi_result=bmi_result,
        category=category,
        history=rows,
        yesterday_entry=yesterday_entry,
        normal_min_weight=normal_min_weight,
        normal_max_weight=normal_max_weight,
        weight_to_lose=weight_to_lose,
        weight_to_gain=weight_to_gain,
        chart_dates=chart_dates,
        chart_bmis=chart_bmis
    )


# ---------- CLEAR HISTORY ----------
@app.route("/bmi/clear", methods=["POST"])
def clear_history():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM bmi_history WHERE user_id = ?", (session["user_id"],))
    conn.commit()
    conn.close()
    flash("History cleared.", "info")
    return redirect(url_for("bmi_page"))


# ---------- AI COACH (PLAN + LIVE CHAT) ----------
@app.route("/coach", methods=["GET", "POST"])
def coach():
    if "user_id" not in session:
        return redirect(url_for("login"))

    week = request.args.get("week", "1")
    try:
        week = int(week)
    except:
        week = 1
    if week not in [1, 2, 3, 4]:
        week = 1

    plan_text = session.get("coach_plan_text")
    weeks = session.get("coach_weeks")
    goal_saved = session.get("coach_goal", "lose weight")
    chat = session.get("coach_chat", [])
    error_msg = None

    # If no plan in session, try loading from DB
    if not plan_text:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT goal, plan_text FROM coach_plans WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (session["user_id"],)
        )
        row = cur.fetchone()
        conn.close()

        if row:
            plan_text = row["plan_text"]
            goal_saved = row["goal"]
            session["coach_plan_text"] = plan_text
            session["coach_goal"] = goal_saved

            parsed = split_plan_into_weeks(plan_text)
            session["coach_weeks"] = {str(k): v for k, v in parsed.items()}
            weeks = session["coach_weeks"]

    if request.method == "POST":
        action = request.form.get("action", "")
        goal = request.form.get("goal", goal_saved)
        session["coach_goal"] = goal

        latest_bmi = get_latest_bmi(session["user_id"])
        if latest_bmi is None:
            error_msg = "Please calculate your BMI at least once before using AI Coach."
        else:
            if action == "generate":
                plan_text = generate_bmi_coach_plan(session["username"], latest_bmi, goal)
                session["coach_plan_text"] = plan_text

                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO coach_plans (user_id, goal, plan_text, created_at) VALUES (?, ?, ?, ?)",
                    (session["user_id"], goal, plan_text, datetime.utcnow().isoformat())
                )
                conn.commit()
                conn.close()

                parsed = split_plan_into_weeks(plan_text)
                session["coach_weeks"] = {str(k): v for k, v in parsed.items()}

                session["coach_chat"] = []
                chat = []

            elif action == "ask":
                question = request.form.get("question", "").strip()
                if not question:
                    error_msg = "Type a question first."
                else:
                    answer = ask_ai_coach(
                        question=question,
                        name=session["username"],
                        bmi=latest_bmi,
                        goal=goal,
                        week=week
                    )

                    chat.append({"role": "user", "content": question})
                    chat.append({"role": "assistant", "content": answer})

                    if len(chat) > 20:
                        chat = chat[-20:]

                    session["coach_chat"] = chat

    if isinstance(weeks, dict):
        current_week_text = weeks.get(str(week)) or weeks.get(week) or ""
    else:
        current_week_text = ""

    if not current_week_text and plan_text:
        current_week_text = plan_text

    return render_template(
        "coach.html",
        plan_text=plan_text,
        goal=goal_saved,
        week=week,
        week_text=current_week_text,
        chat=chat,
        error_msg=error_msg
    )

@app.route("/health")
def health():
    return {
        "status": "ok",
        "openrouter_key_present": bool(
            os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        )
    }

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)
