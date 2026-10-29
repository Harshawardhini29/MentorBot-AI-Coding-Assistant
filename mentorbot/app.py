from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import date
import os, json, requests
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import db

load_dotenv()

# Secure session secret key handling
if not os.getenv("SECRET_KEY"):
    secret_key = os.urandom(24).hex()
    with open(".env", "a") as f:
        f.write(f"\nSECRET_KEY={secret_key}\n")
    load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback_secret_key_123")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Initialize and migrate DB on startup
db.init_db()
db.migrate_old_data()


# ---------------- USER CLASS ----------------
class User(UserMixin):
    def __init__(self, id, xp=0):
        self.id = id
        self.username = id
        self.xp = xp


@login_manager.user_loader
def load_user(user_id):
    u = db.db_get_user(user_id)
    if u:
        return User(u["username"], u["xp"])
    return None


# ---------------- GROQ API HELPER ----------------
def query_groq_model(model, messages, json_mode=False):
    if not GROQ_API_KEY:
        raise ValueError("Groq API Key is missing! Add GROQ_API_KEY to your .env file.")
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": messages
    }
    
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        
    try:
        res = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
        if res.status_code == 200:
            return res.json()["choices"][0]["message"]["content"]
        else:
            raise RuntimeError(f"Groq API Error: {res.status_code} - {res.text}")
    except Exception as e:
        raise RuntimeError(f"Failed to connect to Groq API: {str(e)}")


# ---------------- AUTH ROUTES ----------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
        
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        
        if not username or not password:
            return render_template("signup.html", error="Username and password are required.")
            
        hashed_pw = generate_password_hash(password)
        if db.db_create_user(username, hashed_pw):
            flash("Account created successfully! Please sign in.", "success")
            return redirect(url_for("login"))
        else:
            return render_template("signup.html", error="User already exists!")
            
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
        
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        
        if not username or not password:
            return render_template("login.html", error="Username and password are required.")
            
        user_row = db.db_get_user(username)
        if user_row and check_password_hash(user_row["password_hash"], password):
            user = User(user_row["username"], user_row["xp"])
            login_user(user)
            flash(f"Welcome back, {username}!", "success")
            return redirect(url_for("index"))
        else:
            return render_template("login.html", error="Invalid credentials")
            
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


# ---------------- MAIN CHAT ----------------
@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    username = current_user.id
    today = str(date.today())
    
    if request.method == "POST":
        user_input = request.form.get("user_input", "").strip()
        code_snippet = request.form.get("code_snippet", "").strip()
        
        if not user_input and not code_snippet:
            flash("Please enter a question or provide a code snippet.", "error")
            return redirect(url_for("index"))
            
        if not GROQ_API_KEY:
            flash("API Key is missing from the server!", "error")
            return redirect(url_for("index"))

        # Load conversation history for context (last 6 messages)
        recent_history = db.db_get_user_history(username, limit=6)
        
        system_content = (
            "You are MentorBot, an adaptive AI coding mentor. You must respond ONLY with a JSON object containing "
            "two keys:\n"
            "1. \"level\": Either \"Beginner\", \"Intermediate\", or \"Advanced\", classifying the user's programming level "
            "based on their current input and chat context.\n"
            "2. \"answer\": Your explanation or reply in markdown format. Tailor the content's depth, style, and terminology "
            "specifically to the detected level:\n"
            "   - Beginner: simple language, analogies, basic code examples, avoiding complex jargon.\n"
            "   - Intermediate: technical details, structured code examples, explaining design trade-offs.\n"
            "   - Advanced: deep technical explanations, optimization, advanced patterns, architectural trade-offs.\n\n"
            "Example Output:\n"
            "{\n"
            "  \"level\": \"Beginner\",\n"
            "  \"answer\": \"Recursion is like...\"\n"
            "}"
        )
        
        messages = [{"role": "system", "content": system_content}]
        
        # Append history context
        for msg in recent_history:
            hist_content = msg["question"]
            if msg.get("code_snippet"):
                hist_content += f"\n\nSubmitted Code Snippet:\n```\n{msg['code_snippet']}\n```"
            messages.append({"role": "user", "content": hist_content})
            messages.append({"role": "assistant", "content": msg["answer"]})
            
        # Append current user query
        current_content = user_input
        if code_snippet:
            current_content += f"\n\nSubmitted Code Snippet:\n```\n{code_snippet}\n```"
        messages.append({"role": "user", "content": current_content})
        
        try:
            ai_res_raw = query_groq_model("llama-3.3-70b-versatile", messages, json_mode=True)
            ai_res = json.loads(ai_res_raw)
            ai_reply = ai_res.get("answer", "No reply generated.")
            level = ai_res.get("level", "Beginner")
        except Exception as e:
            flash(f"Error communicating with AI: {str(e)}", "error")
            return redirect(url_for("index"))
            
        # Save to database history
        db.db_add_chat_message(username, today, user_input, code_snippet, ai_reply, level)
        
        # Award +10 XP
        db.db_update_user_xp(username, 10)
        
        # Post-Redirect-Get to prevent duplicate form submissions
        return redirect(url_for("index"))
        
    all_history = db.db_get_all_user_history_grouped_by_date(username)
    chat_history = all_history.get(today, [])
    
    return render_template(
        "index.html",
        username=username,
        xp=current_user.xp,
        chat_history=chat_history,
        all_history=all_history
    )


# ---------------- DAILY CHALLENGE ----------------
@app.route("/daily_challenge")
@login_required
def daily_challenge():
    today = str(date.today())
    username = current_user.id
    
    challenge_row = db.db_get_daily_challenge(username, today)
    
    if not challenge_row:
        if not GROQ_API_KEY:
            flash("API Key is missing from the server!", "error")
            challenge_text = "⚠️ Groq API key is missing. Add it to .env to generate challenges."
        else:
            messages = [
                {"role": "system", "content": "You are a programming mentor. Generate a daily coding challenge. Return a clean challenge in markdown containing: a short problem statement, example input/output, and a hint. Do not include any HTML or preamble."},
                {"role": "user", "content": "Give me today's coding challenge."}
            ]
            try:
                challenge_text = query_groq_model("llama-3.1-8b-instant", messages)
            except Exception as e:
                flash(f"Error generating challenge: {str(e)}", "error")
                challenge_text = "⚠️ Failed to generate today's challenge. Please refresh."
                
        db.db_create_daily_challenge(username, today, challenge_text)
        challenge_row = db.db_get_daily_challenge(username, today)
        
    return render_template(
        "daily.html",
        challenge=challenge_row["challenge_text"],
        date=today,
        solved=challenge_row["solved"],
        code_submission=challenge_row["code_submission"] or "",
        feedback=challenge_row["feedback"] or "",
        xp=current_user.xp
    )


# ---------------- SOLVE CHALLENGE (AJAX) ----------------
@app.route("/solve_challenge", methods=["POST"])
@login_required
def solve_challenge():
    today = str(date.today())
    username = current_user.id
    
    if not request.is_json:
        return jsonify({"success": False, "feedback": "Request must be JSON"}), 400
        
    data = request.get_json()
    code = data.get("code", "").strip()
    
    if not code:
        return jsonify({"success": False, "feedback": "Please write or paste your code solution first."}), 200
        
    challenge_row = db.db_get_daily_challenge(username, today)
    if not challenge_row:
        return jsonify({"success": False, "feedback": "No challenge found for today."}), 404
        
    if not GROQ_API_KEY:
        return jsonify({"success": False, "feedback": "API Key is missing from the server!"}), 500
        
    system_content = (
        "You are an AI coding verification assistant. You are given a coding challenge and a user's code submission.\n"
        "Analyze the code and verify if it correctly and fully solves the challenge.\n"
        "Provide detailed, encouraging, and constructive feedback, identifying any bugs, syntax issues, or "
        "inefficiencies, and recommending best practices.\n"
        "You MUST respond ONLY with a JSON object in this format:\n"
        "{\n"
        "  \"success\": true | false,\n"
        "  \"feedback\": \"Your markdown feedback and suggestions here.\"\n"
        "}"
    )
    
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Challenge:\n{challenge_row['challenge_text']}\n\nUser Submission:\n```\n{code}\n```"}
    ]
    
    try:
        ai_res_raw = query_groq_model("llama-3.3-70b-versatile", messages, json_mode=True)
        ai_res = json.loads(ai_res_raw)
        success = ai_res.get("success", False)
        feedback = ai_res.get("feedback", "No feedback provided.")
    except Exception as e:
        return jsonify({"success": False, "feedback": f"Error running verification: {str(e)}"}), 200
        
    xp_awarded = 0
    # Award +20 XP only if it is the first time solving this challenge
    if success and challenge_row["solved"] == 0:
        db.db_update_user_xp(username, 20)
        xp_awarded = 20
        db.db_update_challenge_solution(username, today, code, feedback, 1)
    else:
        # Save submission and feedback, but keep the current solved state and do not award new XP
        db.db_update_challenge_solution(username, today, code, feedback, challenge_row["solved"])
        
    user_row = db.db_get_user(username)
    new_xp = user_row["xp"] if user_row else 0
    
    return jsonify({
        "success": success,
        "feedback": feedback,
        "xp_awarded": xp_awarded,
        "new_xp": new_xp
    })


# ---------------- BADGES ----------------
@app.route("/badges")
@login_required
def badges():
    xp = current_user.xp
    badges = []
    if xp >= 200:
        badges.append({"icon": "🏆", "name": "Pro Coder", "desc": "Earned 200+ XP", "color": "gold"})
    if xp >= 100:
        badges.append({"icon": "🌟", "name": "Intermediate", "desc": "Earned 100+ XP", "color": "silver"})
    if xp >= 50:
        badges.append({"icon": "⭐", "name": "Beginner", "desc": "Earned 50+ XP", "color": "bronze"})
    return render_template("badges.html", badges=badges, xp=xp)


if __name__ == "__main__":
    app.run(debug=True)