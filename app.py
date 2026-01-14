from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import re
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import secrets
import smtplib
import os
from email.message import EmailMessage

from ai_coach import generate_bmi_coach_plan

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

        password_hash = generate_password_hash(password)

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                        (username, email, password_hash))
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
            cur.execute("INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, ?)",
                        (user["id"], token, expires_at))
            conn.commit()

            reset_url = url_for("reset_password", token=token, _external=True)
            email_body = f"Hi {user['username']},\n\nReset your password (valid {TOKEN_EXPIRATION_MINUTES} min):\n{reset_url}"
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
            return render_template("reset.html")

        pw_hash = generate_password_hash(new_pw)
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


# ---------- AI COACH (WEEK-BY-WEEK) ----------
@app.route("/coach", methods=["GET", "POST"])
def coach():
    if "user_id" not in session:
        return redirect(url_for("login"))

    plan = None
    error_msg = None

    # Which week to show (1..4)
    week_index = int(request.args.get("week", 1))
    if week_index < 1:
        week_index = 1
    if week_index > 4:
        week_index = 4

    if request.method == "POST":
        goal = request.form.get("goal", "lose weight")

        # Get latest BMI for this user
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT bmi FROM bmi_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
            (session["user_id"],)
        )
        row = cur.fetchone()
        conn.close()

        if row:
            plan = generate_bmi_coach_plan(session["username"], row["bmi"], goal)

            # If AI returns an error dict
            if isinstance(plan, dict) and plan.get("error"):
                error_msg = plan["error"]
                plan = None
            else:
                # Save full plan in session so week navigation does not call AI again
                session["coach_plan"] = plan
                session["coach_goal"] = goal
        else:
            error_msg = "Please calculate your BMI at least once before using AI Coach."

    # If user clicks week buttons (GET request), reuse saved plan
    if plan is None and "coach_plan" in session:
        plan = session["coach_plan"]

    # Select the week to display
    week_data = None
    if plan and isinstance(plan, dict) and "weeks" in plan and len(plan["weeks"]) >= 4:
        week_data = plan["weeks"][week_index - 1]

    return render_template(
        "coach.html",
        plan=plan,
        week_data=week_data,
        week_index=week_index,
        error_msg=error_msg
    )


# ---------- BMI CALCULATOR ----------
@app.route("/bmi", methods=["GET", "POST"])
def bmi_page():
    if "user_id" not in session:
        return redirect(url_for("login"))

    error = None
    bmi_result = None
    category = None
    history = []
    yesterday_entry = None

    # Target normal-weight info
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

            if bmi < 18.5:
                cat = "Underweight"
            elif bmi < 25:
                cat = "Normal"
            elif bmi < 30:
                cat = "Overweight"
            else:
                cat = "Obese"

            # Normal BMI target weights for this height
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
                (session["user_id"], weight, height, bmi, cat, timestamp)
            )
            conn.commit()

            bmi_result = bmi
            category = cat

        except Exception:
            error = "Invalid input. Make sure weight/height are valid numbers."

    # Fetch history
    cur.execute("SELECT * FROM bmi_history WHERE user_id = ? ORDER BY timestamp DESC", (session["user_id"],))
    rows = cur.fetchall()
    history = rows

    # Fetch yesterday's BMI
    if rows:
        today = datetime.utcnow().date()
        for r in rows:
            entry_date = datetime.fromisoformat(r["timestamp"]).date()
            if entry_date < today:
                yesterday_entry = r
                break

    conn.close()

    return render_template(
        "bmi.html",
        username=session["username"],
        error=error,
        bmi_result=bmi_result,
        category=category,
        history=history,
        yesterday_entry=yesterday_entry,
        normal_min_weight=normal_min_weight,
        normal_max_weight=normal_max_weight,
        weight_to_lose=weight_to_lose,
        weight_to_gain=weight_to_gain
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


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)
