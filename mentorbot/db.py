import sqlite3
import os
import json
import contextlib
from datetime import datetime, date
from werkzeug.security import generate_password_hash

DB_PATH = "mentorbot.db"

@contextlib.contextmanager
def db_session():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for concurrency safety (multi-user read/write stability)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with db_session() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                xp INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                chat_date TEXT NOT NULL,
                question TEXT NOT NULL,
                code_snippet TEXT,
                answer TEXT NOT NULL,
                level TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (username) REFERENCES users(username)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                challenge_date TEXT NOT NULL,
                challenge_text TEXT NOT NULL,
                solved INTEGER DEFAULT 0,
                code_submission TEXT,
                feedback TEXT,
                solution_date TEXT,
                FOREIGN KEY (username) REFERENCES users(username),
                UNIQUE(username, challenge_date)
            )
        """)
        conn.commit()

def migrate_old_data(users_file="users.json", history_dir="history"):
    if not os.path.exists(users_file):
        return

    print("Starting database migration...")
    
    # Load old users
    try:
        with open(users_file, "r") as f:
            old_users = json.load(f)
    except Exception as e:
        print(f"Error reading users.json: {e}")
        return

    init_db()

    with db_session() as conn:
        for username, plaintext_pw in old_users.items():
            # Check if user already exists
            cur = conn.cursor()
            cur.execute("SELECT username FROM users WHERE username = ?", (username,))
            if cur.fetchone():
                continue
            
            # Hash plaintext password
            pw_hash = generate_password_hash(plaintext_pw)
            
            # Calculate XP based on their history
            history_xp = 0
            history_file = os.path.join(history_dir, f"{username}.json")
            history_entries = []
            
            if os.path.exists(history_file):
                try:
                    with open(history_file, "r") as f:
                        user_history = json.load(f)
                    for chat_date, messages in user_history.items():
                        for msg in messages:
                            history_xp += 10
                            history_entries.append({
                                "date": chat_date,
                                "question": msg.get("question", ""),
                                "answer": msg.get("answer", ""),
                                "level": msg.get("level", "Beginner")
                            })
                except Exception as e:
                    print(f"Error reading history file for {username}: {e}")

            # Insert user with their calculated XP
            conn.execute(
                "INSERT INTO users (username, password_hash, xp) VALUES (?, ?, ?)",
                (username, pw_hash, history_xp)
            )
            
            # Insert history entries
            for entry in history_entries:
                conn.execute(
                    """
                    INSERT INTO chat_history (username, chat_date, question, answer, level)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (username, entry["date"], entry["question"], entry["answer"], entry["level"])
                )
        conn.commit()

    # Rename migrated files to backup
    try:
        backup_users = users_file + ".bak"
        if os.path.exists(users_file):
            os.rename(users_file, backup_users)
            print(f"Renamed {users_file} to {backup_users}")
        
        backup_history = history_dir + ".bak"
        if os.path.exists(history_dir):
            if os.path.exists(backup_history):
                backup_history = f"{history_dir}_{int(datetime.now().timestamp())}.bak"
            os.rename(history_dir, backup_history)
            print(f"Renamed {history_dir} to {backup_history}")
    except Exception as e:
        print(f"Error backing up old files: {e}")

    print("Database migration completed successfully!")

def db_get_user(username):
    with db_session() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

def db_create_user(username, password_hash):
    with db_session() as conn:
        try:
            conn.execute("INSERT INTO users (username, password_hash, xp) VALUES (?, ?, 0)", (username, password_hash))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def db_update_user_xp(username, xp_to_add):
    with db_session() as conn:
        conn.execute("UPDATE users SET xp = xp + ? WHERE username = ?", (xp_to_add, username))
        conn.commit()
        row = conn.execute("SELECT xp FROM users WHERE username = ?", (username,)).fetchone()
        return row["xp"] if row else 0

def db_get_user_history(username, limit=10):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_history WHERE username = ? ORDER BY id DESC LIMIT ?",
            (username, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

def db_get_all_user_history_grouped_by_date(username):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_history WHERE username = ? ORDER BY chat_date DESC, id ASC",
            (username,)
        ).fetchall()
        
        history = {}
        for r in rows:
            chat_date = r["chat_date"]
            if chat_date not in history:
                history[chat_date] = []
            history[chat_date].append({
                "question": r["question"],
                "code_snippet": r["code_snippet"],
                "answer": r["answer"],
                "level": r["level"]
            })
        return history

def db_add_chat_message(username, chat_date, question, code_snippet, answer, level):
    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO chat_history (username, chat_date, question, code_snippet, answer, level)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, chat_date, question, code_snippet, answer, level)
        )
        conn.commit()

def db_get_daily_challenge(username, date_str):
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM daily_challenges WHERE username = ? AND challenge_date = ?",
            (username, date_str)
        ).fetchone()
        return dict(row) if row else None

def db_create_daily_challenge(username, date_str, challenge_text):
    with db_session() as conn:
        try:
            conn.execute(
                "INSERT INTO daily_challenges (username, challenge_date, challenge_text, solved) VALUES (?, ?, ?, 0)",
                (username, date_str, challenge_text)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def db_update_challenge_solution(username, date_str, code_submission, feedback, solved):
    with db_session() as conn:
        sol_date = str(date.today()) if solved else None
        conn.execute(
            """
            UPDATE daily_challenges
            SET code_submission = ?, feedback = ?, solved = ?, solution_date = ?
            WHERE username = ? AND challenge_date = ?
            """,
            (code_submission, feedback, solved, sol_date, username, date_str)
        )
        conn.commit()
