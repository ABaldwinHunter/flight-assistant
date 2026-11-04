import json
import sqlite3

from flight_assistant.store import Store
from flight_assistant.tools import execute_tool


def test_rating_migration_preserves_existing_messages_tools_and_provider_context(settings, search_args):
    transcript = [{"role": "user", "content": "Find simulated flights"}, {"role": "assistant", "content": "Saved flight results"}]
    _, tool_result, _ = execute_tool("search_flights", json.dumps(search_args))
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                lease_token TEXT, lease_until REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL, provider_turn TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE tool_events (
                id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES messages(id),
                name TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL,
                status TEXT NOT NULL, duration_ms REAL NOT NULL
            );
        """)
        connection.execute("INSERT INTO conversations(id, title, created_at) VALUES (?, ?, ?)", ("saved-chat", "Existing conversation", "2026-11-01T12:00:00+00:00"))
        connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)", (7, "saved-chat", "assistant", "Saved flight results", "2026-11-01T12:01:00+00:00", "complete", json.dumps(transcript)))
        connection.execute("INSERT INTO tool_events VALUES (?, ?, ?, ?, ?, ?, ?)", (3, 7, "search_flights", json.dumps(search_args), json.dumps(tool_result), "ok", 0.5))

    migrated = Store(settings.database_path)
    conversation = migrated.get_conversation("saved-chat")
    assert conversation["rating"] is None
    assert conversation["title"] == "Existing conversation"
    message = conversation["messages"][0]
    assert message["id"] == 7 and message["content"] == "Saved flight results"
    assert message["tool_events"][0]["id"] == 3
    assert message["tool_events"][0]["result"] == tool_result
    assert migrated.provider_history("saved-chat", max_turns=12, max_chars=48000) == transcript

    migrated.set_rating("saved-chat", "up")
    restarted = Store(settings.database_path)
    assert restarted.get_conversation("saved-chat") == {**conversation, "rating": "up"}
