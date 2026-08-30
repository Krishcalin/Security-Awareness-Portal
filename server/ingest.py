"""Load authored content into the database.

Runs on start and is idempotent: the same files loaded twice leave the same
rows. Content is authored in files and the database is downstream of them, so
this only ever moves in one direction — nothing here reads a change back out
into `data/`.

The one thing this must not do is destroy evidence. A question that is no
longer authored is RETIRED, never deleted: `response` references `question`
with ON DELETE CASCADE, so deleting a retired question would take every answer
anybody ever gave it along with it. That is precisely the record this product
exists to keep.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from server import content, db

log = logging.getLogger(__name__)

#: Recorded narration, if this deployment has any.
NARRATION = Path(__file__).resolve().parents[1] / "assets" / "narration"


def script_hash(text: str) -> str:
    """Mirrors `script_hash` in tools/build_narration.py, which is where a
    recording's provenance is written down. A test asserts they agree."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def recordings(slug: str) -> Dict[int, Dict[str, Any]]:
    """The recordings this deployment holds for a module.

    Resolved HERE rather than baked into the built content, because which
    recordings exist is a property of the deployment: the same course JSON is
    loaded by a server that has the audio and by one that does not, and the
    second must fall back rather than serve a URL to nothing.

    An entry is dropped if its file is missing. It is dropped in
    `_sync_lessons` if the script has moved on since it was recorded, which is
    the guard that stops a learner hearing one wording while reading another.
    """
    manifest = NARRATION / slug / "manifest.json"
    if not manifest.exists():
        return {}
    slides = json.loads(manifest.read_text(encoding="utf-8")).get("slides", {})
    return {
        int(ordinal): entry for ordinal, entry in slides.items()
        if (NARRATION / slug / entry["file"]).exists()
        # A recording may legitimately carry no word track — see
        # `usable_marks` in tools/build_narration.py. It still plays.
        and (not entry["timings"]
             or (NARRATION / slug / entry["timings"]).exists())
    }


def sync(modules: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Load every authored module. Returns one summary row per module."""
    modules = content.load_modules() if modules is None else modules
    summaries = []
    with db.connection() as conn:
        for order, payload in enumerate(modules):
            summaries.append(_sync_module(conn, payload, order))
        conn.commit()
    for summary in summaries:
        log.info("loaded module", extra={"module": summary["slug"]})
    return summaries


def _sync_module(conn, payload: Dict[str, Any], order: int) -> Dict[str, Any]:
    row = conn.execute(
        """
        INSERT INTO module (slug, title, summary, minutes, topic,
                            content_hash, sort_order)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET
            title        = EXCLUDED.title,
            summary      = EXCLUDED.summary,
            minutes      = EXCLUDED.minutes,
            topic        = EXCLUDED.topic,
            content_hash = EXCLUDED.content_hash,
            sort_order   = EXCLUDED.sort_order,
            updated_at   = now()
        RETURNING id, content_hash
        """,
        (payload["slug"], payload["title"], payload.get("summary", ""),
         payload.get("minutes", 5), payload.get("topic", "general"),
         payload["content_hash"], order),
    ).fetchone()
    module_id = row["id"]

    lessons, recorded = _sync_lessons(conn, module_id, payload["lessons"],
                                      payload["slug"])
    questions, retired = _sync_questions(conn, module_id, payload["questions"])

    # The course is as long as it actually takes here. A recording is usually
    # not the same length as the estimate the build made from the word count,
    # and the figure a learner is shown before pressing play should be the one
    # that applies to what they are about to hear.
    conn.execute(
        """
        UPDATE module SET minutes = GREATEST(1, ROUND((
            SELECT COALESCE(sum(narration_seconds), 0)
            FROM lesson WHERE module_id = %s) / 60.0))
        WHERE id = %s
        """, (module_id, module_id))

    return {
        "slug": payload["slug"],
        "content_hash": payload["content_hash"],
        "lessons": lessons,
        "questions": questions,
        "retired": retired,
        "recorded": recorded,
    }


def _sync_lessons(conn, module_id: int, lessons: List[Dict[str, Any]],
                  slug: str) -> tuple[int, int]:
    available = recordings(slug)
    recorded = 0
    for lesson in lessons:
        # A recording counts only while it is a recording of THESE words.
        # Otherwise it is dropped and the slide is read by the browser: a
        # worse voice, rather than a voice saying something the transcript on
        # screen does not.
        take = available.get(lesson["ordinal"])
        if take and take["script_sha"] != script_hash(lesson.get("narration", "")):
            take = None
        if take:
            recorded += 1
        audio = "narration/%s/%s" % (slug, take["file"]) if take else ""
        timings = ("narration/%s/%s" % (slug, take["timings"])
                   if take and take["timings"] else "")
        seconds = (round(take["seconds"]) if take
                   else lesson.get("narration_seconds", 0))
        conn.execute(
            """
            INSERT INTO lesson (module_id, ordinal, title, body, animation,
                                image, narration, audio_url,
                                audio_timings_url, narration_seconds)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (module_id, ordinal) DO UPDATE SET
                title             = EXCLUDED.title,
                body              = EXCLUDED.body,
                animation         = EXCLUDED.animation,
                image             = EXCLUDED.image,
                narration         = EXCLUDED.narration,
                -- Cleared as well as set: a slide whose script moved on has
                -- no current recording, and must fall back rather than keep
                -- playing the one from before the edit.
                audio_url         = EXCLUDED.audio_url,
                audio_timings_url = EXCLUDED.audio_timings_url,
                narration_seconds = EXCLUDED.narration_seconds
            """,
            (module_id, lesson["ordinal"], lesson["title"],
             lesson.get("body", ""), lesson.get("animation", "none"),
             lesson.get("image", ""), lesson.get("narration", ""),
             audio, timings, seconds),
        )
    # A lesson nothing references can go; only `question` is referenced by the
    # evidence tables.
    ordinals = [l["ordinal"] for l in lessons]
    conn.execute(
        "DELETE FROM lesson WHERE module_id = %s AND ordinal <> ALL(%s)",
        (module_id, ordinals))
    return len(lessons), recorded


def _sync_questions(conn, module_id: int,
                    questions: List[Dict[str, Any]]) -> tuple[int, int]:
    for question in questions:
        conn.execute(
            """
            INSERT INTO question (module_id, ordinal, prompt, options,
                                  correct_index, explains, teaches, retired)
            VALUES (%s, %s, %s, %s, %s, %s, %s, false)
            ON CONFLICT (module_id, ordinal) DO UPDATE SET
                prompt        = EXCLUDED.prompt,
                options       = EXCLUDED.options,
                correct_index = EXCLUDED.correct_index,
                explains      = EXCLUDED.explains,
                teaches       = EXCLUDED.teaches,
                retired       = false
            """,
            (module_id, question["ordinal"], question["prompt"],
             json.dumps(question["options"]), question["correct_index"],
             question.get("explains", ""), question.get("teaches")),
        )
    ordinals = [q["ordinal"] for q in questions]
    retired = conn.execute(
        """
        UPDATE question SET retired = true
        WHERE module_id = %s AND ordinal <> ALL(%s) AND NOT retired
        RETURNING id
        """,
        (module_id, ordinals)).fetchall()
    return len(questions), len(retired)
