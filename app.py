import os
import json
from flask import Flask, request, jsonify, send_from_directory


app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production")


import os
from flask import render_template

import os
import json
from flask import Flask, request, jsonify, render_template



# grading scheme
EVALUATING_EXAM = {
    "Part 1: Pharmaceutical Sciences": {"weight": 25},
    "Part 2: Pharmacy Practice": {"weight": 55},
    "Part 3: Social / Behavioural / Administrative Sciences": {"weight": 20},
}

app = Flask(__name__, template_folder="templates")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"ok": True})



def safe_num(value, default=0):
    try:
        n = float(value)
        return max(0, min(100, round(n, 1)))
    except (TypeError, ValueError):
        return default


def compute_basic_results(payload):
    part_scores = payload.get("partScores", {})
    chapter_attempts = payload.get("chapterAttempts", {})
    attempt_timeline = payload.get("attemptTimeline", [])

    section_rows = []
    weighted_sum = 0
    coverage = 0

    for part, meta in EVALUATING_EXAM.items():
        weight = meta["weight"]
        score = part_scores.get(part)

        if score is None:
            # fallback to latest chapter averages if available
            chapter_scores = []
            for key, attempts in chapter_attempts.items():
                if not key.startswith(part + "|||"):
                    continue
                if attempts:
                    latest = attempts[-1].get("score")
                    if latest is not None:
                        chapter_scores.append(safe_num(latest))

            score = round(sum(chapter_scores) / len(chapter_scores), 1) if chapter_scores else None

        if score is not None:
            score = safe_num(score)
            contribution = round(score * weight / 100, 1)
            weighted_sum += contribution
            coverage += weight
        else:
            contribution = 0

        section_rows.append({
            "part": part,
            "weight": weight,
            "score": score,
            "contribution": contribution
        })

    weighted_grade = round(weighted_sum / (coverage / 100), 1) if coverage else None
    projected_grade = round(weighted_sum, 1)

    strengths = sorted(
        [row for row in section_rows if row["score"] is not None],
        key=lambda x: x["score"],
        reverse=True
    )

    weakest = sorted(
        [row for row in section_rows if row["score"] is not None],
        key=lambda x: x["score"]
    )

    avg_attempt = None
    if attempt_timeline:
        vals = [safe_num(x.get("score")) for x in attempt_timeline if x.get("score") is not None]
        if vals:
            avg_attempt = round(sum(vals) / len(vals), 1)

    return {
        "weighted_grade": weighted_grade,
        "projected_grade": projected_grade,
        "coverage": coverage,
        "avg_attempt": avg_attempt,
        "sections": section_rows,
        "strongest_section": strengths[0]["part"] if strengths else None,
        "weakest_section": weakest[0]["part"] if weakest else None,
        "total_attempts": len(attempt_timeline),
    }


def build_ai_prompt(basic):
    return f"""
You are helping interpret pharmacy exam prep results.

Rules:
- Be concise and discrete.
- Do not sound dramatic.
- Give a short professional interpretation in 3 parts:
  1. summary
  2. strengths
  3. focus_next
- Each part must be short.
- Base everything only on the data provided.
- Do not invent clinical claims or unsafe advice.
- If coverage is low, explicitly say interpretation is preliminary.
- Output valid JSON only with keys:
  summary, strengths, focus_next

Data:
{json.dumps(basic, ensure_ascii=False)}
""".strip()


@app.route("/api/results/basic", methods=["POST"])
def basic_results():
    payload = request.get_json(force=True) or {}
    basic = compute_basic_results(payload)
    return jsonify({"ok": True, "basic": basic})




if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)