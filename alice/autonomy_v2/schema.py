"""Additive v2 storage: waits, receipts, family windows, idle, views, invitations.

Original turns, runs and memories are never overwritten or deleted by this
schema; every table here is new and namespaced with a ``v2_`` prefix. Old
binaries ignore these tables, so absence of rows means "not migrated", not
"empty data".
"""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS v2_question_waits (
 question_id INTEGER PRIMARY KEY,
 kind TEXT NOT NULL,
 reason TEXT NOT NULL,
 context_hint TEXT NOT NULL DEFAULT '',
 related_refs TEXT NOT NULL DEFAULT '[]',
 after_user_turn_id INTEGER,
 not_before_at REAL,
 set_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS v2_progress_receipts (
 run_id TEXT PRIMARY KEY,
 question_id INTEGER,
 decision TEXT NOT NULL,
 progress_kind TEXT NOT NULL,
 summary TEXT NOT NULL,
 actual_execution INTEGER NOT NULL DEFAULT 0,
 substantive_progress INTEGER NOT NULL DEFAULT 0,
 resulting_status TEXT,
 external_material_version TEXT NOT NULL DEFAULT '',
 working_revision TEXT NOT NULL DEFAULT '',
 remaining_family_windows INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL DEFAULT '',
 created_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS v2_family_windows (
 family_id TEXT NOT NULL,
 external_version TEXT NOT NULL,
 windows INTEGER NOT NULL DEFAULT 0,
 bonus INTEGER NOT NULL DEFAULT 0,
 last_run_id TEXT,
 updated_at REAL NOT NULL DEFAULT 0,
 PRIMARY KEY (family_id, external_version)
);
CREATE TABLE IF NOT EXISTS v2_activity_idle (
 scope TEXT PRIMARY KEY,
 started_at REAL NOT NULL,
 until_at REAL NOT NULL,
 reason TEXT NOT NULL,
 related_run_id TEXT
);
CREATE TABLE IF NOT EXISTS v2_question_families (
 question_id INTEGER PRIMARY KEY,
 family_id TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '',
 set_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS v2_view_heads (
 question_id INTEGER PRIMARY KEY,
 view_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 memory_id INTEGER,
 updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS v2_view_revisions (
 view_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 question_id INTEGER NOT NULL,
 memory_id INTEGER,
 change_reason TEXT NOT NULL DEFAULT '',
 previous_revision INTEGER,
 created_at REAL NOT NULL DEFAULT 0,
 PRIMARY KEY (view_id, revision)
);
CREATE TABLE IF NOT EXISTS v2_contact_intents (
 id TEXT PRIMARY KEY,
 epoch TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1,
 motive TEXT NOT NULL DEFAULT 'share',
 topic TEXT NOT NULL,
 reason TEXT NOT NULL,
 content_refs TEXT NOT NULL DEFAULT '[]',
 basis TEXT,
 state TEXT NOT NULL DEFAULT 'active',
 question_id INTEGER,
 question_version INTEGER,
 view_id TEXT,
 view_revision INTEGER,
 next_eligible_at REAL,
 legacy_contact_id INTEGER,
 created_at REAL NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS v2_invitation_attempts (
 id TEXT PRIMARY KEY,
 intent_id TEXT NOT NULL,
 intent_version INTEGER NOT NULL DEFAULT 1,
 epoch TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL DEFAULT 'prepared',
 invitation_text TEXT NOT NULL,
 prepared_at REAL NOT NULL DEFAULT 0,
 delivered_at REAL,
 expires_at REAL,
 invitation_turn_id INTEGER,
 reply_user_turn_id INTEGER,
 expansion_request_id TEXT,
 expansion_turn_id INTEGER,
 deferred_reason TEXT,
 created_at REAL NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS v2_attempt_epoch_state ON v2_invitation_attempts(epoch,state,updated_at);
CREATE INDEX IF NOT EXISTS v2_intent_epoch_state ON v2_contact_intents(epoch,state,updated_at);
CREATE TABLE IF NOT EXISTS v2_contact_decisions (
 run_id TEXT PRIMARY KEY,
 epoch TEXT NOT NULL,
 decision TEXT NOT NULL,
 reason TEXT NOT NULL,
 outcome TEXT NOT NULL,
 intent_id TEXT,
 attempt_id TEXT,
 blockers TEXT NOT NULL DEFAULT '[]',
 basis TEXT NOT NULL DEFAULT '{}',
 created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS v2_decision_epoch ON v2_contact_decisions(epoch,created_at);
CREATE TABLE IF NOT EXISTS v2_contact_holds (
 epoch TEXT NOT NULL, channel_id TEXT NOT NULL, intent_id TEXT NOT NULL DEFAULT '',
 reason TEXT NOT NULL, attempt_id TEXT, after_user_turn_id INTEGER NOT NULL DEFAULT 0,
 resume_at REAL, updated_at REAL NOT NULL,
 PRIMARY KEY(epoch,channel_id,intent_id)
);
CREATE TABLE IF NOT EXISTS v2_activity_plans (
 run_id TEXT PRIMARY KEY, epoch TEXT NOT NULL, question_id INTEGER,
 choice TEXT NOT NULL, object TEXT NOT NULL, value TEXT NOT NULL,
 why_now TEXT NOT NULL, stop_condition TEXT NOT NULL, next_action TEXT NOT NULL,
 basis TEXT NOT NULL, result TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS v2_plan_epoch ON v2_activity_plans(epoch,created_at);
"""


def install(conn) -> None:
    conn.executescript(SCHEMA)
