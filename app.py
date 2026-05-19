import os
import json
import io
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production")

DEFAULT_EXAM = {
    "Part 1: Pharmaceutical Sciences": {"weight": 25},
    "Part 2: Pharmacy Practice": {"weight": 55},
    "Part 3: Social / Behavioural / Administrative Sciences": {"weight": 20},
}


def safe_num(value, default=0):
    try:
        n = float(value)
        return max(0, min(100, round(n, 1)))
    except (TypeError, ValueError):
        return default


def get_exam_parts(payload):
    """Use the course structure sent from the browser, with a fallback for older clients."""
    course_parts = payload.get("courseParts") or payload.get("examParts") or {}
    if isinstance(course_parts, dict) and course_parts:
        normalized = {}
        for part, meta in course_parts.items():
            if isinstance(meta, dict):
                normalized[part] = {
                    "weight": safe_num(meta.get("weight"), 0),
                    "chapter_count": int(meta.get("chapter_count") or meta.get("chapterCount") or 0),
                }
            else:
                normalized[part] = {"weight": safe_num(meta, 0), "chapter_count": 0}
        return normalized
    return {part: {"weight": meta["weight"], "chapter_count": 0} for part, meta in DEFAULT_EXAM.items()}


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"ok": True})


def compute_basic_results(payload):
    exam_parts = get_exam_parts(payload)
    part_scores = payload.get("partScores", {}) or {}
    chapter_attempts = payload.get("chapterAttempts", {}) or {}
    attempt_timeline = payload.get("attemptTimeline", []) or []

    section_rows = []
    weighted_sum = 0
    coverage = 0

    for part, meta in exam_parts.items():
        weight = safe_num(meta.get("weight"), 0)
        score = part_scores.get(part)

        if score is None:
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
            "contribution": contribution,
            "chapter_count": meta.get("chapter_count", 0),
        })

    weighted_grade = round(weighted_sum / (coverage / 100), 1) if coverage else None
    projected_grade = round(weighted_sum, 1)

    ranked = sorted(
        [row for row in section_rows if row["score"] is not None],
        key=lambda x: x["score"],
        reverse=True,
    )

    vals = [safe_num(x.get("score")) for x in attempt_timeline if x.get("score") is not None]
    avg_attempt = round(sum(vals) / len(vals), 1) if vals else None

    return {
        "weighted_grade": weighted_grade,
        "projected_grade": projected_grade,
        "coverage": round(coverage, 1),
        "avg_attempt": avg_attempt,
        "sections": section_rows,
        "strongest_section": ranked[0]["part"] if ranked else None,
        "weakest_section": ranked[-1]["part"] if ranked else None,
        "total_attempts": len(attempt_timeline),
    }


@app.route("/api/results/basic", methods=["POST"])
def basic_results():
    payload = request.get_json(force=True) or {}
    basic = compute_basic_results(payload)
    return jsonify({"ok": True, "basic": basic})


def build_local_recommendations(basic):
    sections = basic.get("sections", [])
    missing = [s for s in sections if s.get("score") is None]
    weak = sorted([s for s in sections if s.get("score") is not None], key=lambda x: x["score"] or 0)[:2]

    lines = []
    if missing:
        lines.append("Add scores for the missing sections so the final result is based on full coverage.")
    if weak:
        names = ", ".join(s["part"] for s in weak)
        lines.append(f"Prioritize review for: {names}.")
    if basic.get("avg_attempt") is not None:
        if basic["avg_attempt"] < 70:
            lines.append("Redo missed chapter questions and aim for at least 70% before moving on.")
        else:
            lines.append("Keep reviewing missed questions while maintaining your current attempt average.")
    if not lines:
        lines.append("Start entering part scores or chapter attempts to unlock more useful recommendations.")
    return "\n".join(f"- {line}" for line in lines)


def generate_ai_recommendations(payload, basic):
    """Optional OpenAI summary. If OPENAI_API_KEY is not set, the app still works."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None

    try:
        from openai import OpenAI

        client = OpenAI()
        model = os.environ.get("OPENAI_RESULTS_MODEL", "gpt-4.1-mini")
        compact_payload = {
            "course": payload.get("course"),
            "basic_results": basic,
            "recent_attempts": (payload.get("attemptTimeline") or [])[-12:],
        }
        response = client.responses.create(
            model=model,
            input=(
                "Write a short, professional student exam-results summary. "
                "Use plain text only. Include strengths, weak areas, and 3 action steps. "
                "Do not invent scores. Data:\n"
                + json.dumps(compact_payload, ensure_ascii=False)
            ),
        )
        text = getattr(response, "output_text", None)
        return text.strip() if text else None
    except Exception as exc:
        return f"AI recommendations unavailable: {exc}"


def build_results_text(payload, basic, recommendations):
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    course = payload.get("course") or "Selected course"
    chapter_attempts = payload.get("chapterAttempts", {}) or {}
    feedback = payload.get("chapterFeedback", {}) or {}

    lines = []
    lines.append("PharmacyPrep Student Results Summary")
    lines.append("=" * 40)
    lines.append(f"Course: {course}")
    lines.append(f"Generated: {generated_at}")
    lines.append("")
    lines.append("Overall Results")
    lines.append("-" * 16)
    lines.append(f"Current overall grade: {basic['projected_grade']}%")
    lines.append(f"Weighted grade on completed sections: {basic['weighted_grade'] if basic['weighted_grade'] is not None else 'N/A'}")
    lines.append(f"Grade coverage: {basic['coverage']}%")
    lines.append(f"Total attempts: {basic['total_attempts']}")
    lines.append(f"Average attempt score: {basic['avg_attempt'] if basic['avg_attempt'] is not None else 'N/A'}")
    lines.append(f"Strongest section: {basic['strongest_section'] or 'N/A'}")
    lines.append(f"Weakest section: {basic['weakest_section'] or 'N/A'}")
    lines.append("")
    lines.append("Section Breakdown")
    lines.append("-" * 17)
    for row in basic["sections"]:
        score = f"{row['score']}%" if row["score"] is not None else "Not entered"
        lines.append(f"{row['part']}")
        lines.append(f"  Weight: {row['weight']}%")
        lines.append(f"  Score: {score}")
        lines.append(f"  Final-grade contribution: {row['contribution']}%")
    lines.append("")
    lines.append("Latest Chapter Attempts")
    lines.append("-" * 23)

    latest_rows = []
    for key, attempts in chapter_attempts.items():
        if not attempts:
            continue
        latest = attempts[-1]
        part, chapter = key.split("|||", 1) if "|||" in key else (latest.get("part", ""), latest.get("chapter", key))
        latest_rows.append((part, chapter, latest))

    if latest_rows:
        for part, chapter, latest in latest_rows:
            lines.append(f"{chapter}")
            lines.append(f"  Section: {part}")
            lines.append(f"  Latest score: {latest.get('earned')}/{latest.get('possible')} ({latest.get('score')}%)")
            if latest.get("quickSource"):
                lines.append(f"  Source: {latest.get('quickSource')}")
    else:
        lines.append("No chapter attempts entered yet.")

    if feedback:
        reaction_labels = {"up": "above 70%", "neutral": "50-60%", "down": "below 50%"}
        lines.append("")
        lines.append("Emoji Quick-Score Notes")
        lines.append("-" * 23)
        for key, reaction in feedback.items():
            chapter = key.split("|||", 1)[1] if "|||" in key else key
            lines.append(f"{chapter}: {reaction_labels.get(reaction, reaction)}")

    lines.append("")
    lines.append("Recommendations")
    lines.append("-" * 15)
    lines.append(recommendations or build_local_recommendations(basic))
    lines.append("")
    return "\n".join(lines)


@app.route("/api/results/download", methods=["POST"])
def download_results():
    payload = request.get_json(force=True) or {}
    basic = compute_basic_results(payload)
    recommendations = generate_ai_recommendations(payload, basic) or build_local_recommendations(basic)
    report = build_results_text(payload, basic, recommendations)

    safe_course = "student-results"
    if payload.get("course"):
        safe_course = "".join(ch.lower() if ch.isalnum() else "-" for ch in payload["course"]).strip("-")[:70]
    filename = f"{safe_course}-results-summary.txt"

    buffer = io.BytesIO(report.encode("utf-8"))
    return send_file(
        buffer,
        mimetype="text/plain; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    app.run(host="0.0.0.0", port=port, debug=True)
