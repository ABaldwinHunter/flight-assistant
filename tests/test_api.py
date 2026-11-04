from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from flight_assistant.app import create_app
from conftest import ScriptedProvider, answer, calls


def test_configuration_and_missing_key_do_not_prevent_startup(settings):
    provider = ScriptedProvider()
    with TestClient(create_app(replace(settings, api_key=None), provider=provider)) as client:
        config = client.get("/api/config").json()
        assert config["llm_configured"] is False
        assert config["data_source"] == "simulated"
        assert config["demo_date"] == "2026-11-16"
        assert config["tool_failure_enabled"] is False
        assert "api_key" not in config
        conversation = client.post("/api/conversations", json={}).json()
        assert conversation["messages"] == []
        response = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "Hello"})
        assert response.status_code == 200
        assert response.json()["assistant_message"]["status"] == "error"
        assert "OPENAI_API_KEY" in response.json()["assistant_message"]["content"]
        assert provider.calls == []
        saved = client.get(f"/api/conversations/{conversation['id']}").json()
        assert len(saved["messages"]) == 2
        assert saved["title"] == "Hello"


def test_successful_http_turn_has_durable_tool_events_and_title(settings, search_args):
    provider = ScriptedProvider(calls(("search_flights", search_args, "search-1")), answer("Here are the simulated flight options."))
    with TestClient(create_app(settings, provider=provider)) as client:
        created = client.post("/api/conversations", json={})
        assert created.status_code == 201
        conversation_id = created.json()["id"]
        turn = client.post(f"/api/conversations/{conversation_id}/messages", json={"content": "JFK to LHR on 2026-11-16"})
        assert turn.status_code == 200
        assistant = turn.json()["assistant_message"]
        assert assistant["status"] == "complete"
        assert assistant["tool_events"][0]["name"] == "search_flights"
        assert turn.json()["user_message"]["tool_events"] == []
        saved = client.get(f"/api/conversations/{conversation_id}").json()
        assert saved["messages"][-1] == assistant
        assert saved["title"] == "JFK to LHR on 2026-11-16"
        listed = client.get("/api/conversations").json()["conversations"]
        assert listed[0]["id"] == conversation_id


@pytest.mark.parametrize("body", [{"content": ""}, {"content": " \n "}, {"content": "x" * 4001}, {"content": 123}, {}, {"content": "Hi", "extra": True}])
def test_invalid_message_body_returns_safe_string_detail_without_writes(settings, body):
    with TestClient(create_app(settings, provider=ScriptedProvider())) as client:
        conversation_id = client.post("/api/conversations", json={}).json()["id"]
        response = client.post(f"/api/conversations/{conversation_id}/messages", json=body)
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)
        assert client.get(f"/api/conversations/{conversation_id}").json()["messages"] == []


def test_unknown_ids_and_concurrent_turns_have_meaningful_status(settings):
    application = create_app(settings, provider=ScriptedProvider())
    with TestClient(application) as client:
        assert client.get("/api/conversations/not-a-real-id").status_code == 404
        assert client.post("/api/conversations/not-a-real-id/messages", json={"content": "Hello"}).status_code == 404
        conversation_id = client.post("/api/conversations", json={}).json()["id"]
        token, _ = application.state.service.store.begin_turn(conversation_id, "In flight")
        response = client.post(f"/api/conversations/{conversation_id}/messages", json={"content": "Another request"})
        assert response.status_code == 409
        assert isinstance(response.json()["detail"], str)
        assert len(client.get(f"/api/conversations/{conversation_id}").json()["messages"]) == 1
        application.state.service.store.release_turn(conversation_id, token)


def test_failure_config_is_explicit_and_credentials_are_not_returned(settings):
    with TestClient(create_app(replace(settings, simulate_failure=True), provider=ScriptedProvider())) as client:
        response = client.get("/api/config")
        assert response.json()["tool_failure_enabled"] is True
        assert settings.api_key not in response.text


def test_ratings_can_change_clear_and_survive_restart_without_crossing_conversations(settings):
    with TestClient(create_app(settings, provider=ScriptedProvider())) as client:
        first = client.post("/api/conversations", json={}).json()
        second = client.post("/api/conversations", json={}).json()
        assert first["id"] != second["id"]
        assert first["rating"] is None

    for rating in ("up", "down", None):
        with TestClient(create_app(settings, provider=ScriptedProvider())) as client:
            response = client.put(f"/api/conversations/{first['id']}/rating", json={"rating": rating})
            assert response.status_code == 200
            assert response.json() == {"id": first["id"], "rating": rating}
        with TestClient(create_app(settings, provider=ScriptedProvider())) as restarted:
            saved = restarted.get(f"/api/conversations/{first['id']}").json()
            assert saved["rating"] == rating
            assert saved["messages"] == []
            assert restarted.get(f"/api/conversations/{second['id']}").json()["rating"] is None
            listed = restarted.get("/api/conversations").json()["conversations"]
            assert next(item for item in listed if item["id"] == first["id"])["rating"] == rating


@pytest.mark.parametrize("body", [
    {}, {"rating": "sideways"}, {"rating": 0}, {"rating": True},
    {"rating": []}, {"rating": {}}, {"rating": "up", "extra": True},
])
def test_invalid_ratings_do_not_replace_saved_feedback(settings, body):
    with TestClient(create_app(settings, provider=ScriptedProvider())) as client:
        conversation = client.post("/api/conversations", json={}).json()
        path = f"/api/conversations/{conversation['id']}/rating"
        client.put(path, json={"rating": "up"})
        response = client.put(path, json=body)
        assert response.status_code == 422
        assert client.get(f"/api/conversations/{conversation['id']}").json()["rating"] == "up"


def test_rating_unknown_conversation_returns_not_found(settings):
    with TestClient(create_app(settings, provider=ScriptedProvider())) as client:
        assert client.put("/api/conversations/missing/rating", json={"rating": "up"}).status_code == 404
        assert client.get("/api/conversations").json()["conversations"] == []
