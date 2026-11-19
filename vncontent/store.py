"""Hàng đợi công việc trong SQLite: mỗi job một video đi qua các bước ingest -> dub -> render -> meta -> publish."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

STAGES = ("ingest", "dub", "render", "meta", "publish")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source  TEXT NOT NULL,
    kind    TEXT NOT NULL,              -- url | file
    rights  TEXT NOT NULL DEFAULT '',   -- ghi chú quyền sử dụng nội dung
    status  TEXT NOT NULL DEFAULT 'pending',   -- pending | running | failed | done
    stage   TEXT NOT NULL DEFAULT '',   -- bước đã xong gần nhất
    data    TEXT NOT NULL DEFAULT '{}', -- JSON: đường dẫn kết quả từng bước, ghi đè của người dùng
    error   TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
)
"""


@dataclass
class Job:
    id: int
    source: str
    kind: str
    rights: str = ""
    status: str = "pending"
    stage: str = ""
    data: dict = field(default_factory=dict)
    error: str = ""
    created: float = 0.0
    updated: float = 0.0

    def next_stage(self) -> str | None:
        i = STAGES.index(self.stage) + 1 if self.stage else 0
        return STAGES[i] if i < len(STAGES) else None


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(_SCHEMA)
        self.db.commit()

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        d = dict(row)
        d["data"] = json.loads(d["data"] or "{}")
        return Job(**d)

    def add(self, source: str, kind: str, rights: str = "", data: dict | None = None) -> Job:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO jobs(source, kind, rights, data, created, updated) VALUES (?,?,?,?,?,?)",
            (source, kind, rights, json.dumps(data or {}, ensure_ascii=False), now, now))
        self.db.commit()
        return self.get(cur.lastrowid)

    def get(self, job_id: int) -> Job:
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Không có job #{job_id}")
        return self._job(row)

    def all(self, status: str | None = None) -> list[Job]:
        q, args = "SELECT * FROM jobs", ()
        if status:
            q, args = q + " WHERE status=?", (status,)
        return [self._job(r) for r in self.db.execute(q + " ORDER BY id", args)]

    def update(self, job_id: int, *, merge: dict | None = None, **fields) -> Job:
        job = self.get(job_id)
        if merge:
            job.data.update(merge)
            fields["data"] = json.dumps(job.data, ensure_ascii=False)
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        self.db.commit()
        return self.get(job_id)

    def close(self) -> None:
        self.db.close()
