"""SQLite 持久层：建表在服务启动时执行，合法设计与审计结果按 ID 存取。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join("data", "weave.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    cyclic      INTEGER NOT NULL,
    max_float   INTEGER NOT NULL,
    shafts      INTEGER NOT NULL,
    treadles    INTEGER NOT NULL,
    warp_ends   INTEGER NOT NULL,
    weft_picks  INTEGER NOT NULL,
    threading   TEXT NOT NULL,   -- JSON
    tie_up      TEXT NOT NULL,   -- JSON
    treadling   TEXT NOT NULL,   -- JSON
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS audits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id    INTEGER NOT NULL UNIQUE REFERENCES designs (id) ON DELETE CASCADE,
    exceeds      INTEGER NOT NULL,
    interlacing  TEXT NOT NULL,  -- JSON
    violations   TEXT NOT NULL,  -- JSON
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """启动时建表（幂等）。"""
    directory = os.path.dirname(DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)


def insert_design_with_audit(
    *,
    name: str | None,
    cyclic: bool,
    max_float: int,
    shafts: int,
    treadles: int,
    threading_map: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    interlacing: list[list[int]],
    violations: list[dict],
) -> int:
    """同事务写入设计与审计结果，返回设计 ID。"""
    with _lock, _connect() as conn:
        cur = conn.execute(
            """INSERT INTO designs
               (name, cyclic, max_float, shafts, treadles, warp_ends, weft_picks,
                threading, tie_up, treadling)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                int(cyclic),
                max_float,
                shafts,
                treadles,
                len(threading_map),
                len(treadling),
                json.dumps(threading_map),
                json.dumps(tie_up),
                json.dumps(treadling),
            ),
        )
        design_id = cur.lastrowid
        conn.execute(
            """INSERT INTO audits (design_id, exceeds, interlacing, violations)
               VALUES (?, ?, ?, ?)""",
            (
                design_id,
                int(bool(violations)),
                json.dumps(interlacing),
                json.dumps(violations),
            ),
        )
        return design_id


def _row_to_detail(design: sqlite3.Row, audit: sqlite3.Row) -> dict:
    return {
        "id": design["id"],
        "name": design["name"],
        "cyclic": bool(design["cyclic"]),
        "max_float": design["max_float"],
        "shafts": design["shafts"],
        "treadles": design["treadles"],
        "warp_ends": design["warp_ends"],
        "weft_picks": design["weft_picks"],
        "threading": json.loads(design["threading"]),
        "tie_up": json.loads(design["tie_up"]),
        "treadling": json.loads(design["treadling"]),
        "created_at": design["created_at"],
        "audit": {
            "exceeds": bool(audit["exceeds"]),
            "interlacing": json.loads(audit["interlacing"]),
            "violations": json.loads(audit["violations"]),
        },
    }


def get_design(design_id: int) -> dict | None:
    with _lock, _connect() as conn:
        design = conn.execute(
            "SELECT * FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
        if design is None:
            return None
        audit = conn.execute(
            "SELECT * FROM audits WHERE design_id = ?", (design_id,)
        ).fetchone()
        return _row_to_detail(design, audit)


def list_designs() -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            """SELECT d.id, d.name, d.cyclic, d.max_float, d.shafts, d.treadles,
                      d.warp_ends, d.weft_picks, d.created_at, a.exceeds
               FROM designs d JOIN audits a ON a.design_id = d.id
               ORDER BY d.id"""
        ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "cyclic": bool(r["cyclic"]),
                "max_float": r["max_float"],
                "shafts": r["shafts"],
                "treadles": r["treadles"],
                "warp_ends": r["warp_ends"],
                "weft_picks": r["weft_picks"],
                "exceeds": bool(r["exceeds"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
