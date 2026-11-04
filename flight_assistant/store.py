"""SQLite history, complete provider transcripts, and per-conversation turn leases."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import time
from uuid import uuid4


class ConversationNotFound(Exception):
    pass


class ConversationBusy(Exception):
    pass


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path):
        self.path = str(path)
        from pathlib import Path
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    lease_token TEXT,
                    lease_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('complete', 'error')),
                    provider_turn TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, id);
                CREATE TABLE IF NOT EXISTS tool_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    result TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ok', 'error')),
                    duration_ms REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tool_events_message ON tool_events(message_id, id);
                CREATE INDEX IF NOT EXISTS conversations_created ON conversations(created_at DESC);
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_conversation(self) -> dict:
        conversation = {"id": str(uuid4()), "title": "New conversation", "created_at": timestamp()}
        with self.connection() as connection:
            connection.execute("INSERT INTO conversations(id, title, created_at) VALUES (?, ?, ?)", tuple(conversation.values()))
        return {**conversation, "messages": []}

    def list_conversations(self) -> list[dict]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT id, title, created_at FROM conversations ORDER BY created_at DESC, rowid DESC")]

    @staticmethod
    def _message(connection, row) -> dict:
        result = {key: row[key] for key in ("id", "role", "content", "created_at", "status")}
        result["tool_events"] = [
            {"id": event["id"], "name": event["name"], "arguments": json.loads(event["arguments"]), "result": json.loads(event["result"]), "status": event["status"], "duration_ms": event["duration_ms"]}
            for event in connection.execute("SELECT * FROM tool_events WHERE message_id = ? ORDER BY id", (row["id"],))
        ]
        return result

    def get_conversation(self, conversation_id: str) -> dict:
        with self.connection() as connection:
            row = connection.execute("SELECT id, title, created_at FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row is None:
                raise ConversationNotFound
            messages = connection.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
            return {**dict(row), "messages": [self._message(connection, message) for message in messages]}

    def begin_turn(self, conversation_id: str, content: str) -> tuple[str, dict]:
        token = str(uuid4())
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row is None:
                raise ConversationNotFound
            if row["lease_until"] > time.time():
                raise ConversationBusy
            # Longer than the bounded five 20-second provider calls; survives multiworker access.
            connection.execute("UPDATE conversations SET lease_token = ?, lease_until = ? WHERE id = ?", (token, time.time() + 180, conversation_id))
            existing = connection.execute("SELECT 1 FROM messages WHERE conversation_id = ? LIMIT 1", (conversation_id,)).fetchone()
            if existing is None:
                title = " ".join(content.split())[:64]
                connection.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
            cursor = connection.execute("INSERT INTO messages(conversation_id, role, content, created_at, status) VALUES (?, 'user', ?, ?, 'complete')", (conversation_id, content, timestamp()))
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return token, self._message(connection, row)

    def provider_history(self, conversation_id: str, *, max_turns: int, max_chars: int) -> list[dict]:
        """Keep only whole completed turns, so function calls always have matching outputs."""
        with self.connection() as connection:
            rows = connection.execute("SELECT provider_turn FROM messages WHERE conversation_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT ?", (conversation_id, max_turns)).fetchall()
        selected = []
        size = 0
        for row in rows:
            raw = row["provider_turn"]
            if size + len(raw) > max_chars:
                break
            selected.append(json.loads(raw))
            size += len(raw)
        return [item for turn in reversed(selected) for item in turn]

    def finish_turn(self, conversation_id: str, token: str, content: str, status: str, events: list[dict], provider_turn: list[dict]) -> dict:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute("SELECT lease_token FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if lease is None:
                raise ConversationNotFound
            if lease["lease_token"] != token:
                raise ConversationBusy
            cursor = connection.execute("INSERT INTO messages(conversation_id, role, content, created_at, status, provider_turn) VALUES (?, 'assistant', ?, ?, ?, ?)", (conversation_id, content, timestamp(), status, json.dumps(provider_turn, allow_nan=False)))
            message_id = cursor.lastrowid
            for event in events:
                connection.execute("INSERT INTO tool_events(message_id, name, arguments, result, status, duration_ms) VALUES (?, ?, ?, ?, ?, ?)", (message_id, event["name"], json.dumps(event["arguments"], allow_nan=False), json.dumps(event["result"], allow_nan=False), event["status"], event["duration_ms"]))
            connection.execute("UPDATE conversations SET lease_token = NULL, lease_until = 0 WHERE id = ? AND lease_token = ?", (conversation_id, token))
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            return self._message(connection, row)

    def release_turn(self, conversation_id: str, token: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE conversations SET lease_token = NULL, lease_until = 0 WHERE id = ? AND lease_token = ?", (conversation_id, token))
