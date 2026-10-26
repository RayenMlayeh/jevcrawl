"""SQLite answer cache: a repeated goal on the same pages costs $0."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

MAX_AGE_SECONDS = 30 * 24 * 3600  # keys include the goal, so old answers are rarely reused
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
        self._db.execute(
            "DELETE FROM decisions WHERE created_at < ?", (time.time() - MAX_AGE_SECONDS,)
        )
        self._db.commit()

    def get(self, key: str) -> float | None:
        """The cached noul for ``key``, if any."""
        row = self._db.execute("SELECT answer FROM decisions WHERE key = ?", (key,)).fetchone()
        return float(json.loads(row[0])["noul"]) if row else None

    def put(self, model: str, answers: dict[str, float]) -> None:
        """Store ``{key: noul}`` in one transaction; ``model`` is the versioned id behind them."""
        now = time.time()
        self._db.executemany(
            "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?)",
            [(key, model, json.dumps({"noul": noul}), now) for key, noul in answers.items()],
        )
        self._db.commit()

    def close(self) -> None:
        """Close the database."""
        self._db.close()
