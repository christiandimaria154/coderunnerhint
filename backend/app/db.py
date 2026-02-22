from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'hint_engine.sqlite3'


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                mode TEXT NOT NULL,
                language TEXT NOT NULL,
                course_id INTEGER,
                quiz_id INTEGER,
                question_id INTEGER,
                question_slot INTEGER,
                question_name TEXT,
                student_id TEXT NOT NULL,
                attempt_id INTEGER,
                attempt_no INTEGER,
                source_code TEXT,
                source_hash TEXT,
                score REAL,
                max_score REAL,
                compile_error_text TEXT,
                runtime_error_text TEXT,
                failed_tests_json TEXT,
                full_feedback_text TEXT,
                cluster_key TEXT,
                hint_level INTEGER,
                hint_type TEXT,
                hint_variant TEXT,
                hint_text TEXT,
                confidence REAL,
                improved_vs_previous INTEGER DEFAULT NULL,
                delta_score REAL DEFAULT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_attempts_student_q
                ON attempts (student_id, language, quiz_id, question_id, question_slot, id);

            CREATE TABLE IF NOT EXISTS hint_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language TEXT NOT NULL,
                cluster_key TEXT NOT NULL,
                hint_level INTEGER NOT NULL,
                hint_variant TEXT NOT NULL,
                exposures INTEGER NOT NULL DEFAULT 0,
                improvements INTEGER NOT NULL DEFAULT 0,
                total_delta REAL NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(language, cluster_key, hint_level, hint_variant)
            );
            '''
        )


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def insert_attempt(row: dict[str, Any]) -> int:
    keys = list(row.keys())
    placeholders = ', '.join('?' for _ in keys)
    sql = f"INSERT INTO attempts ({', '.join(keys)}) VALUES ({placeholders})"
    with get_conn() as conn:
        cur = conn.execute(sql, [row[k] for k in keys])
        return int(cur.lastrowid)


def get_last_attempt_for_context(*, student_id: str, language: str, quiz_id: int, question_id: int, question_slot: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute(
            '''
            SELECT * FROM attempts
            WHERE student_id = ? AND language = ? AND quiz_id = ?
              AND COALESCE(question_id, 0) = ? AND COALESCE(question_slot, 0) = ?
            ORDER BY id DESC LIMIT 1
            ''',
            (student_id, language, quiz_id, question_id, question_slot)
        )
        return cur.fetchone()


def update_attempt_improvement(attempt_row_id: int, improved: bool, delta_score: float) -> None:
    with get_conn() as conn:
        conn.execute(
            'UPDATE attempts SET improved_vs_previous = ?, delta_score = ? WHERE id = ?',
            (1 if improved else 0, float(delta_score), attempt_row_id)
        )


def bump_hint_stats(*, language: str, cluster_key: str, hint_level: int, hint_variant: str, exposure_inc: int = 0, improvement_inc: int = 0, delta_inc: float = 0.0) -> None:
    with get_conn() as conn:
        conn.execute(
            '''
            INSERT INTO hint_stats (language, cluster_key, hint_level, hint_variant, exposures, improvements, total_delta)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(language, cluster_key, hint_level, hint_variant)
            DO UPDATE SET
                exposures = exposures + excluded.exposures,
                improvements = improvements + excluded.improvements,
                total_delta = total_delta + excluded.total_delta,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (language, cluster_key, hint_level, hint_variant, exposure_inc, improvement_inc, float(delta_inc))
        )


def get_hint_stats(*, language: str, cluster_key: str, hint_level: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute(
            '''
            SELECT * FROM hint_stats
            WHERE language = ? AND cluster_key = ? AND hint_level = ?
            ORDER BY exposures DESC, improvements DESC, total_delta DESC
            ''',
            (language, cluster_key, hint_level)
        )
        return cur.fetchall()


def json_text(obj: Any) -> str:
    return _json_dumps(obj)
