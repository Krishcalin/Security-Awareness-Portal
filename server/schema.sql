-- Security Awareness Portal — schema.
--
-- Applied idempotently on start: every statement is CREATE ... IF NOT EXISTS or
-- ALTER ... ADD COLUMN IF NOT EXISTS, so the same file upgrades an existing
-- database and creates a new one.
--
-- THE ONE IDEA THIS SCHEMA IS BUILT AROUND
--
-- Awareness training is sold and reported on completion: "94% of staff are
-- trained." Completion measures that somebody reached the last page. It does
-- not measure that they can spot a phish, and a portal that reports the first
-- as the second tells a CISO their organisation is safe on evidence that does
-- not support it.
--
-- So completion and learning are SEPARATE things here and are never collapsed:
--
--   enrolment          did this person open it, and did they reach the end
--   attempt / response did they answer correctly, on which try, and how fast
--   question stats     does this question discriminate at all, or does everyone
--                      get it right and learn nothing from being asked
--
-- A question that 100% of people answer correctly measures nothing. It is not a
-- pass; it is an unasked question, and `question_stat` exists to say so.

-- ── content ────────────────────────────────────────────────────────────────
-- Content is authored as files under data/modules and loaded on start. The
-- database records what was loaded so a result can be traced to the exact
-- version of the material that produced it — a score against content nobody
-- can reconstruct is not evidence of anything.

CREATE TABLE IF NOT EXISTS module (
    id            bigserial PRIMARY KEY,
    slug          text NOT NULL UNIQUE,
    title         text NOT NULL,
    summary       text NOT NULL DEFAULT '',
    -- Minutes, as authored. Shown to the learner so "this will take a moment"
    -- is a promise rather than a surprise.
    minutes       integer NOT NULL DEFAULT 5,
    -- The threat this module is about: phishing, passwords, data-handling…
    topic         text NOT NULL DEFAULT 'general',
    -- Content hash of the authored file. A result carries the version it was
    -- earned against, so editing a module does not silently rewrite history.
    content_hash  text NOT NULL,
    sort_order    integer NOT NULL DEFAULT 100,
    published     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lesson (
    id            bigserial PRIMARY KEY,
    module_id     bigint NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    ordinal       integer NOT NULL,
    title         text NOT NULL,
    body          text NOT NULL DEFAULT '',
    -- Which animation this screen plays. Named rather than embedded so the
    -- content stays readable and the motion stays in the frontend.
    animation     text NOT NULL DEFAULT 'none',
    -- The authored artwork for this slide, relative to assets/.
    image         text NOT NULL DEFAULT '',
    -- The voice-over script, spoken by the browser rather than shipped as
    -- audio: no files to host, works offline, and the words stay diffable in
    -- review. `audio_url` is the escape hatch for recorded narration later —
    -- when it is set the player prefers it, so swapping in a real voice is a
    -- content change and not a schema one.
    narration     text NOT NULL DEFAULT '',
    audio_url     text NOT NULL DEFAULT '',
    -- Roughly how long the narration takes, from its own word count. Shown so
    -- a learner knows what they are committing to before they press play.
    narration_seconds integer NOT NULL DEFAULT 0,
    UNIQUE (module_id, ordinal)
);

CREATE TABLE IF NOT EXISTS question (
    id            bigserial PRIMARY KEY,
    module_id     bigint NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    ordinal       integer NOT NULL,
    prompt        text NOT NULL,
    -- Options and the correct index. Stored as JSON because the shape is
    -- authored content, not a relation anybody queries across.
    options       jsonb NOT NULL DEFAULT '[]'::jsonb,
    correct_index integer NOT NULL,
    -- Shown AFTER answering, right or wrong. A quiz that says only "incorrect"
    -- teaches nothing; the explanation is the part that does the work.
    explains      text NOT NULL DEFAULT '',
    UNIQUE (module_id, ordinal)
);

-- ── people ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS learner (
    id            bigserial PRIMARY KEY,
    email         text NOT NULL UNIQUE,
    -- Entra ID's immutable object id for this person. The KEY that identities
    -- should be matched on: an email address is a display attribute in Entra
    -- and changes on marriage, transfer or rebrand, and matching on it would
    -- silently split one person's training record in two.
    entra_oid     text UNIQUE,
    -- The userPrincipalName as Entra reports it. Stored for support, never
    -- used for matching, for the same reason.
    upn           text NOT NULL DEFAULT '',
    display_name  text NOT NULL DEFAULT '',
    -- Free text, because every organisation slices itself differently and a
    -- fixed hierarchy would be wrong for most of them.
    department    text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS learner_department_idx ON learner (department);

-- ── completion: did they get to the end ────────────────────────────────────

CREATE TABLE IF NOT EXISTS enrolment (
    id            bigserial PRIMARY KEY,
    learner_id    bigint NOT NULL REFERENCES learner(id) ON DELETE CASCADE,
    module_id     bigint NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    started_at    timestamptz,
    completed_at  timestamptz,
    -- The furthest lesson reached. Kept separately from completed_at so
    -- "opened it and stopped on screen two" is visible rather than rounding to
    -- "not completed", which is what hides a module people cannot finish.
    furthest_ordinal integer NOT NULL DEFAULT 0,
    UNIQUE (learner_id, module_id)
);

CREATE INDEX IF NOT EXISTS enrolment_module_idx ON enrolment (module_id);

-- ── learning: could they answer, and on which try ──────────────────────────

CREATE TABLE IF NOT EXISTS attempt (
    id            bigserial PRIMARY KEY,
    learner_id    bigint NOT NULL REFERENCES learner(id) ON DELETE CASCADE,
    module_id     bigint NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    -- 1 for the first sitting, 2 for the retake, and so on. THE number that
    -- separates "knew it" from "clicked until it went green": a pass on the
    -- third attempt is not the same evidence as a pass on the first, and a
    -- portal that stores only the best score cannot tell them apart.
    attempt_no    integer NOT NULL DEFAULT 1,
    -- The content hash the attempt was made against.
    content_hash  text NOT NULL DEFAULT '',
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    score         integer,
    out_of        integer,
    UNIQUE (learner_id, module_id, attempt_no)
);

CREATE INDEX IF NOT EXISTS attempt_module_idx ON attempt (module_id);

CREATE TABLE IF NOT EXISTS response (
    id            bigserial PRIMARY KEY,
    attempt_id    bigint NOT NULL REFERENCES attempt(id) ON DELETE CASCADE,
    question_id   bigint NOT NULL REFERENCES question(id) ON DELETE CASCADE,
    chosen_index  integer,
    correct       boolean NOT NULL,
    -- Milliseconds on the question. An answer in under two seconds is a guess
    -- or a remembered position, and neither is knowledge.
    took_ms       integer,
    answered_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (attempt_id, question_id)
);

CREATE INDEX IF NOT EXISTS response_question_idx ON response (question_id);

-- ── whether the questions are worth asking ─────────────────────────────────
--
-- Derived from responses, not authored. A question everybody answers correctly
-- on the first attempt discriminates nothing: it cannot distinguish a person
-- who understands from one who does not, so a high score on it is not evidence
-- of awareness. This is where the portal reports on itself.

CREATE OR REPLACE VIEW question_stat AS
SELECT q.id                                        AS question_id,
       q.module_id,
       q.ordinal,
       q.prompt,
       count(r.id)                                 AS answered,
       count(r.id) FILTER (WHERE r.correct)        AS correct,
       CASE WHEN count(r.id) = 0 THEN NULL
            ELSE round(count(r.id) FILTER (WHERE r.correct)::numeric
                       / count(r.id), 3) END       AS correct_rate,
       count(r.id) FILTER (WHERE r.took_ms IS NOT NULL AND r.took_ms < 2000)
                                                   AS answered_under_2s
FROM question q
LEFT JOIN response r ON r.question_id = q.id
GROUP BY q.id, q.module_id, q.ordinal, q.prompt;

-- ── keeping the promises made above ────────────────────────────────────────
--
-- Two of them are not kept by loading edited content over the top of old
-- content, which is what the loader does every time a module is re-authored.

-- "A result carries the version it was earned against, so editing a module
-- does not silently rewrite history." `attempt.content_hash` records WHICH
-- version, but the question rows are overwritten in place, so the wording that
-- was actually on the screen is gone. This keeps it: what the person was
-- asked, and what the options were, as at the moment they answered.
ALTER TABLE response ADD COLUMN IF NOT EXISTS
    asked jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Deleting a question that is no longer authored would cascade to `response`
-- and destroy exactly the evidence this schema exists to keep. Questions are
-- retired instead — they stop being asked and stay answerable-about.
ALTER TABLE question ADD COLUMN IF NOT EXISTS
    retired boolean NOT NULL DEFAULT false;

-- Which lesson this question tests. Without it a low correct rate is a verdict
-- on the people who answered; with it, it points at the slide that failed to
-- teach. That is the difference between a report that improves the course and
-- one that just ranks staff.
ALTER TABLE question ADD COLUMN IF NOT EXISTS
    teaches integer;

-- ── the name that goes on a certificate ────────────────────────────────────
--
-- Entra's `name` claim is a display name, and a display name is whatever the
-- directory has been told to show — "de, Krishnendu (Security)" in plenty of
-- tenants. A certificate needs the given and family names as separate claims.
ALTER TABLE learner ADD COLUMN IF NOT EXISTS given_name  text NOT NULL DEFAULT '';
ALTER TABLE learner ADD COLUMN IF NOT EXISTS family_name text NOT NULL DEFAULT '';

-- ── coming back to where you left ──────────────────────────────────────────
--
-- Kept separately from `furthest_ordinal`, which is a high-water mark and must
-- not go down. Somebody who reaches slide 18, scrolls back to 4 to re-read it
-- and closes the tab left at 4 — that is where they should resume, while their
-- progress is still 18.
ALTER TABLE enrolment ADD COLUMN IF NOT EXISTS
    last_ordinal integer NOT NULL DEFAULT 0;
ALTER TABLE enrolment ADD COLUMN IF NOT EXISTS
    last_seen_at timestamptz;

-- ── certificates ───────────────────────────────────────────────────────────
--
-- Issued on a passing attempt and never regenerated from live data. A
-- certificate is a statement about a moment: it records the name AS PRINTED
-- and the score as earned, so that somebody changing their surname next year
-- does not retrospectively alter the document they were sent, and re-rendering
-- it cannot quietly produce something different from what was emailed.

CREATE TABLE IF NOT EXISTS certificate (
    id            bigserial PRIMARY KEY,
    -- Quoted on the certificate so a holder, or their manager, can check one
    -- that arrived by email against what this database says was issued.
    serial        text NOT NULL UNIQUE,
    learner_id    bigint NOT NULL REFERENCES learner(id) ON DELETE CASCADE,
    module_id     bigint NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    -- One per passing attempt: a retake that passes again does not silently
    -- replace the record of the attempt that earned the first one.
    attempt_id    bigint NOT NULL UNIQUE REFERENCES attempt(id) ON DELETE CASCADE,
    name_printed  text NOT NULL,
    score         integer NOT NULL,
    out_of        integer NOT NULL,
    issued_at     timestamptz NOT NULL DEFAULT now(),
    -- Delivery is recorded, including its failures. An email that bounced and
    -- an email that was never attempted look identical from the outside, and
    -- both look like a certificate that was sent.
    emailed_to    text NOT NULL DEFAULT '',
    emailed_at    timestamptz,
    email_error   text NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS certificate_learner_idx ON certificate (learner_id);
CREATE INDEX IF NOT EXISTS certificate_unsent_idx ON certificate (issued_at)
    WHERE emailed_at IS NULL;

-- Where each word falls in the recording, as a path to a small JSON track.
-- Without it a recorded slide loses the transcript highlight that the browser
-- synthesiser gave for free through its word-boundary events — and that
-- highlight is what makes the course usable by somebody who is reading rather
-- than listening.
ALTER TABLE lesson ADD COLUMN IF NOT EXISTS
    audio_timings_url text NOT NULL DEFAULT '';

-- ── which ten questions this attempt asked ─────────────────────────────────
--
-- The bank holds a hundred; a learner answers ten of them, drawn when the
-- attempt starts. The draw is RECORDED rather than recomputed, for two
-- reasons. Somebody who closes the tab half way through must come back to the
-- same ten in the same order, not a fresh draw that discards their answers.
-- And `response` has to be checkable against what was actually asked, or a
-- learner could answer questions that were never drawn for them.

CREATE TABLE IF NOT EXISTS attempt_question (
    attempt_id  bigint NOT NULL REFERENCES attempt(id) ON DELETE CASCADE,
    -- Where it fell in this learner's ten. The order is part of the draw.
    position    integer NOT NULL,
    question_id bigint NOT NULL REFERENCES question(id) ON DELETE CASCADE,
    PRIMARY KEY (attempt_id, position),
    UNIQUE (attempt_id, question_id)
);

-- A fingerprint of the ordered draw. The unique index below is what makes
-- "no two attempts get the same ten in the same order" a fact rather than a
-- probability: a repeat cannot be stored, so the draw is simply taken again.
-- With a hundred questions there are about 6e19 possible ordered tens, so
-- this is not expected to fire in the lifetime of the service — it is here so
-- that the guarantee does not depend on that expectation being right.
ALTER TABLE attempt ADD COLUMN IF NOT EXISTS
    question_set text NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS attempt_question_set_idx
    ON attempt (module_id, question_set) WHERE question_set <> '';
