"""SQLite answer cache: a repeated goal on the same pages costs $0."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    key TEXT PRIMARY KEY,
    model TEXT,
    answer TEXT,
    created_at REAL
)
"""


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decision_key(model: str, state: Any, question: Any) -> str:
    """``sha256(model + canonical_json(state) + canonical_json(question))``."""
    payload = model + canonical_json(state) + canonical_json(question)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DecisionCache:
    """Maps a decision key to the noul answer Jev gave for it."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(_SCHEMA)
        self._db.commit()

    def get(self, key: str) -> float | None:
        """The cached noul for ``key``, if any."""
        row = self._db.execute("SELECT answer FROM decisions WHERE key = ?", (key,)).fetchone()
        return float(json.loads(row[0])["noul"]) if row else None

    def put(self, key: str, model: str, noul: float) -> None:
        """Store an answer; ``model`` is the versioned id that produced it."""
        self._db.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?)",
            (key, model, json.dumps({"noul": noul}), time.time()),
        )
        self._db.commit()

    def close(self) -> None:
        """Close the database."""
        self._db.close()
