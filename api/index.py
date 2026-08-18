"""
Human vs AI Image Game — Web Version (Vercel + Neon)
=====================================
"""

import os
import re
import random
import time
from datetime import datetime
import psycopg2
import psycopg2.extras

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, abort, send_file
)

app = Flask(__name__)
app.secret_key = "humanai-web-secret-2026"

# Look in api/img (inside serverless bundle) with fallback to public/img
API_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_FOLDER = os.path.join(API_DIR, "img")
PUBLIC_IMG_FOLDER = os.path.join(os.path.dirname(API_DIR), "public", "img")
ORIG_SUFFIXES   = ("_orig", "_original")
AI_SUFFIXES     = ("_ai",)
IMAGE_EXTS      = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
NUM_ROUNDS      = 5
ADMIN_PASSWORD  = "5598"
DATABASE_URL    = os.environ.get("DATABASE_URL")

# ── Helpers ───────────────────────────────────────────────────────────────────
def get_db_connection():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL)

def _strip_suffix(basename: str, suffixes: tuple) -> str | None:
    for sfx in suffixes:
        if basename.lower().endswith(sfx):
            return basename[: len(basename) - len(sfx)]
    return None

def load_pairs() -> list:
    pairs = {
        "Stonehenge": {"original": "Stonehenge_orig.jpg", "ai": "Stonehenge_ai.png"},
        "Sydney": {"original": "Sydney_orig.jpg", "ai": "Sydney_ai.png"},
        "albert": {"original": "albert_orig.png", "ai": "albert_ai.png"},
        "gothic": {"original": "gothic_orig.webp", "ai": "gothic_ai.png"},
        "monalisa": {"original": "monalisa_orig.jpg", "ai": "monalisa_ai.png"},
        "pearl": {"original": "pearl_orig.jpeg", "ai": "pearl_ai.png"},
        "school": {"original": "school_orig.webp", "ai": "school_ai.png"},
        "scream": {"original": "scream_orig.jpeg", "ai": "scream_ai.png"},
        "self": {"original": "self_orig.avif", "ai": "self_ai.webp"},
        "starrynight": {"original": "starrynight_orig.jpg", "ai": "starrynight_ai.png"},
        "water": {"original": "water_orig.jpg", "ai": "water_ai.png"},
        "wave": {"original": "wave_orig.jpeg", "ai": "wave_ai.png"},
    }
    return [(k, v) for k, v in pairs.items()]

def format_elapsed(ms: int) -> str:
    total_s, rem_ms = divmod(int(ms), 1000)
    mins, secs = divmod(total_s, 60)
    cs = rem_ms // 10
    if mins:
        return f"{mins}m {secs:02d}.{cs:02d}s"
    return f"{secs}.{cs:02d}s"

def _load_scores() -> list:
    if not DATABASE_URL:
        return []
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT name as \"Name\", email as \"Email\", score as \"Score\", total as \"Total\", percentage as \"Percentage\", time_taken as \"TimeTaken\", to_char(created_at, 'YYYY-MM-DD HH24:MI') as \"Date\" FROM scores ORDER BY score DESC, time_taken ASC LIMIT 20")
        scores = cur.fetchall()
        cur.close()
        conn.close()
        return scores
    except Exception as e:
        print("Error loading scores:", e)
        return []

def _save_score(name: str, email: str, score: int, elapsed_ms: int) -> None:
    if not DATABASE_URL:
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        percentage = f"{score * 100 // NUM_ROUNDS}%"
        time_taken = format_elapsed(elapsed_ms)
        cur.execute(
            "INSERT INTO scores (name, email, score, total, percentage, time_taken) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (email) DO UPDATE SET score = EXCLUDED.score, percentage = EXCLUDED.percentage, time_taken = EXCLUDED.time_taken",
            (name, email, score, NUM_ROUNDS, percentage, time_taken)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Error saving score:", e)

def _build_rounds(pairs: list) -> list:
    selected = random.sample(pairs, NUM_ROUNDS)
    rounds = []
    for key, imgs in selected:
        left_is_orig = random.choice([True, False])
        rounds.append({
            "key":          key,
            "left":         imgs["original"] if left_is_orig else imgs["ai"],
            "right":        imgs["ai"]       if left_is_orig else imgs["original"],
            "left_is_orig": left_is_orig,
        })
    return rounds

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def welcome():
    return render_template("welcome.html")

@app.route("/start", methods=["POST"])
def start():
    name  = request.form.get("name",  "").strip()
    email = request.form.get("email", "").strip().lower()

    errors = []
    if not name:  errors.append("Name is required.")
    if not email: errors.append("Email address is required.")
    elif not EMAIL_RE.match(email): errors.append("Please enter a valid email address.")

    # Check for duplicate email — one participation per email
    if not errors and DATABASE_URL:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id FROM scores WHERE email = %s", (email,))
            if cur.fetchone():
                errors.append("This email has already been used. Each person may participate only once.")
            cur.close()
            conn.close()
        except Exception as e:
            print("Error checking email:", e)

    pairs = []
    if not errors:
        pairs = load_pairs()
        if not pairs:
            errors.append("No image pairs found. Check the image folder on the server.")
        elif len(pairs) < NUM_ROUNDS:
            errors.append(f"Need at least {NUM_ROUNDS} image pairs; found only {len(pairs)}.")

    if errors:
        return render_template("welcome.html", errors=errors,
                               name=name, email=email)

    session.clear()
    session["name"]          = name
    session["email"]         = email
    session["rounds"]        = _build_rounds(pairs)
    session["current_round"] = 0
    session["score"]         = 0
    session["start_time"]    = time.time()
    session["elapsed_ms"]    = 0
    session["answers_given"] = []
    session["scored"]        = False
    return redirect(url_for("play"))

@app.route("/play")
def play():
    if "rounds" not in session:
        return redirect(url_for("welcome"))
    idx = session.get("current_round", 0)
    if idx >= NUM_ROUNDS:
        return redirect(url_for("results"))
    r = session["rounds"][idx]
    return render_template("round.html",
        round_num=idx + 1,
        total_rounds=NUM_ROUNDS,
        score=session.get("score", 0),
        left_img=r["left"],
        right_img=r["right"],
    )

@app.route("/answer", methods=["POST"])
def answer():
    if "rounds" not in session:
        return jsonify({"error": "no session"}), 400

    data       = request.get_json(silent=True) or {}
    chosen     = data.get("choice")
    elapsed_ms = int(data.get("elapsed_ms", 0))

    idx = session.get("current_round", 0)
    if idx >= NUM_ROUNDS:
        return jsonify({"error": "game over"}), 400

    r = session["rounds"][idx]
    correct = (chosen == "left") == r["left_is_orig"]

    answers_given = session.get("answers_given", [])
    if idx not in answers_given:
        if correct:
            session["score"] = session.get("score", 0) + 1
        answers_given.append(idx)
        session["answers_given"] = answers_given

    session["elapsed_ms"] = elapsed_ms

    return jsonify({
        "correct":      correct,
        "correct_side": "left" if r["left_is_orig"] else "right",
        "score":        session["score"],
    })

@app.route("/next", methods=["POST"])
def next_round():
    if "rounds" not in session:
        return redirect(url_for("welcome"))
    elapsed_ms = request.form.get("elapsed_ms", 0)
    session["elapsed_ms"]    = int(elapsed_ms)
    session["current_round"] = session.get("current_round", 0) + 1
    if session["current_round"] >= NUM_ROUNDS:
        return redirect(url_for("results"))
    return redirect(url_for("play"))

@app.route("/results")
def results():
    if "rounds" not in session:
        return redirect(url_for("welcome"))

    score      = session.get("score", 0)
    elapsed_ms = session.get("elapsed_ms") or int(
        (time.time() - session.get("start_time", time.time())) * 1000
    )
    pct = score * 100 // NUM_ROUNDS

    if   pct == 100: verdict, vcolor = "Perfect score! You can always spot the AI.", "#4caf50"
    elif pct >= 80:  verdict, vcolor = "Great eye! You're very good at this.",       "#4caf50"
    elif pct >= 60:  verdict, vcolor = "Not bad — the AI is getting better though…", "#ffca28"
    elif pct >= 40:  verdict, vcolor = "Tricky, isn't it? AI fooled you several times.", "#ff7043"
    else:            verdict, vcolor = "The AI had you completely fooled! Try again?", "#e94560"

    if not session.get("scored"):
        _save_score(session["name"], session["email"],
                    score, elapsed_ms)
        session["scored"] = True

    return render_template("results.html",
        name=session["name"],
        score=score,
        total=NUM_ROUNDS,
        pct=pct,
        verdict=verdict,
        verdict_color=vcolor,
        time_str=format_elapsed(elapsed_ms),
    )

@app.route("/play-again", methods=["POST"])
def play_again():
    if "rounds" not in session:
        return redirect(url_for("welcome"))
    pairs = load_pairs()
    if len(pairs) < NUM_ROUNDS:
        return redirect(url_for("welcome"))
    session["rounds"]        = _build_rounds(pairs)
    session["current_round"] = 0
    session["score"]         = 0
    session["start_time"]    = time.time()
    session["elapsed_ms"]    = 0
    session["answers_given"] = []
    session["scored"]        = False
    return redirect(url_for("play"))

@app.route("/img/<path:filename>")
def serve_image(filename):
    safe_name = os.path.basename(filename)
    for folder in (IMAGE_FOLDER, PUBLIC_IMG_FOLDER):
        full_path = os.path.join(folder, safe_name)
        if os.path.isfile(full_path):
            return send_file(full_path)
    abort(404)

@app.route("/leaderboard")
def leaderboard():
    return render_template("leaderboard.html", scores=_load_scores(), error=None)

@app.route("/leaderboard/reset", methods=["POST"])
def reset_leaderboard():
    pwd = request.form.get("password", "")
    if pwd == ADMIN_PASSWORD:
        if DATABASE_URL:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("DELETE FROM scores")
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print("Error resetting leaderboard:", e)
        return redirect(url_for("leaderboard"))
    return render_template("leaderboard.html",
                           scores=_load_scores(),
                           error="Incorrect password.")

@app.route("/game")
def game_html():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "game.html"))

@app.route("/api/pairs")
def api_pairs():
    pairs = load_pairs()
    return jsonify([{"original": v["original"], "ai": v["ai"]} for _, v in pairs])

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5001)
