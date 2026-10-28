"""SQLite makes request identity and saved-plan uniqueness transactional."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .domain import canonical, digest


class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS plans (request_id TEXT PRIMARY KEY, plan TEXT NOT NULL)")
        self.connection.commit()

    def begin(self):
        # Prototype deliberately serializes writers, including model calls.
        self.connection.execute("BEGIN IMMEDIATE")

    def load(self, request: dict) -> dict | None:
        row = self.connection.execute("SELECT fingerprint, state FROM requests WHERE id=?", (request["request_id"],)).fetchone()
        if not row:
            return None
        if row[0] != digest(request):
            raise ValueError("request_id 已绑定其他输入；修改需求请使用新的 request_id")
        return json.loads(row[1])

    def save_plan(self, request_id: str, plan: dict) -> dict:
        old = self.connection.execute("SELECT plan FROM plans WHERE request_id=?", (request_id,)).fetchone()
        if old:
            existing = json.loads(old[0])
            if canonical(existing) != canonical(plan):
                raise ValueError("该请求已有不同的保存方案，禁止覆盖")
            return existing
        self.connection.execute("INSERT INTO plans VALUES (?, ?)", (request_id, canonical(plan)))
        return plan

    def checkpoint(self, request: dict, state: dict):
        self.connection.execute("INSERT INTO requests VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET state=excluded.state", (request["request_id"], digest(request), canonical(state)))

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()
