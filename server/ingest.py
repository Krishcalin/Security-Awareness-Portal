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

import json
import logging
from typing import Any, Dict, List, Optional

from server import content, db

log = logging.getLogger(__name__)


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

    lessons = _sync_lessons(conn, module_id, payload["lessons"])
    questions, retired = _sync_questions(conn, module_id, payload["questions"])
    return {
        "slug": payload["slug"],
        "content_hash": payload["content_hash"],
        "lessons": lessons,
        "questions": questions,
        "retired": retired,
    }


def _sync_lessons(conn, module_id: int, lessons: List[Dict[str, Any]]) -> int:
    for lesson in lessons:
        conn.execute(
            """
            INSERT INTO lesson (module_id, ordinal, title, body, animation,
                                image, narration, audio_url, narration_seconds)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (module_id, ordinal) DO UPDATE SET
                title             = EXCLUDED.title,
                body              = EXCLUDED.body,
                animation         = EXCLUDED.animation,
                image             = EXCLUDED.image,
                narration         = EXCLUDED.narration,
                narration_seconds = EXCLUDED.narration_seconds
            """,
            (module_id, lesson["ordinal"], lesson["title"],
             lesson.get("body", ""), lesson.get("animation", "none"),
             lesson.get("image", ""), lesson.get("narration", ""),
             lesson.get("audio_url", ""), lesson.get("narration_seconds", 0)),
        )
    # A lesson nothing references can go; only `question` is referenced by the
    # evidence tables.
    ordinals = [l["ordinal"] for l in lessons]
    conn.execute(
        "DELETE FROM lesson WHERE module_id = %s AND ordinal <> ALL(%s)",
        (module_id, ordinals))
    return len(lessons)


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
