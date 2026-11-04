from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from flight_assistant.service import ChatService, MAX_CONTEXT_CHARS
from flight_assistant.store import ConversationBusy, Store
from conftest import ScriptedProvider, answer, calls


def new_chat(settings, provider):
    service = ChatService(settings, provider=provider)
    return service, service.store.create_conversation()["id"]


def test_search_trace_and_grounded_followup_survive_restart(settings, search_args):
    provider = ScriptedProvider(calls(("search_flights", search_args, "search-1")), answer("Here are the simulated flight options."))
    service, chat_id = new_chat(settings, provider)
    result = service.send_message(chat_id, "JFK to LHR on November 16, 2026")
    assistant = result["assistant_message"]
    assert assistant["status"] == "complete"
    assert len(assistant["tool_events"]) == 1
    event = assistant["tool_events"][0]
    assert event["status"] == "ok" and event["duration_ms"] >= 0
    assert event["arguments"] == search_args
    cheapest = event["result"]["flights"][0]

    followup = ScriptedProvider(calls(("get_flight_details", {"flight_id": cheapest["flight_id"]}, "detail-1")), answer("Here are the simulated baggage details."))
    restarted = ChatService(settings, provider=followup)
    result = restarted.send_message(chat_id, "Does the cheaper one include a checked bag?")
    assert result["assistant_message"]["status"] == "complete"
    prior_outputs = [item for item in followup.calls[0]["input"] if item.get("type") == "function_call_output"]
    assert json.loads(prior_outputs[0]["output"])["flights"][0] == cheapest
    restored = restarted.store.get_conversation(chat_id)
    assert len(restored["messages"]) == 4
    assert restored["messages"][1] == assistant
    assert restored["messages"][3]["tool_events"][0]["result"]["flight"]["baggage"]


def test_empty_search_and_tool_failure_are_distinct(settings, search_args):
    provider = ScriptedProvider(calls(("search_flights", {**search_args, "max_price": 1}, "empty")), answer("No simulated matches under USD 1."))
    service, chat_id = new_chat(settings, provider)
    result = service.send_message(chat_id, "JFK-LHR November 16, 2026 under USD 1")
    event = result["assistant_message"]["tool_events"][0]
    assert event["status"] == "ok" and event["result"]["flights"] == []

    failed_provider = ScriptedProvider(calls(("search_flights", search_args, "failed")), answer("The simulated flight tool is unavailable; I have no flight evidence."))
    failed = ChatService(replace(settings, simulate_failure=True), provider=failed_provider)
    result = failed.send_message(chat_id, "Try again without the price limit")
    event = result["assistant_message"]["tool_events"][0]
    assert event["status"] == "error"
    assert event["result"]["error"]["code"] == "simulated_failure"
    output = failed_provider.calls[1]["input"][-1]
    assert json.loads(output["output"])["error"]["code"] == "simulated_failure"


def test_invalid_tool_arguments_can_be_corrected_without_crashing(settings, search_args):
    provider = ScriptedProvider(calls(("search_flights", "{", "bad")), calls(("search_flights", search_args, "good")), answer("Simulated search complete."))
    service, chat_id = new_chat(settings, provider)
    result = service.send_message(chat_id, "JFK to LHR 2026-11-16")
    assert result["assistant_message"]["status"] == "complete"
    assert [event["status"] for event in result["assistant_message"]["tool_events"]] == ["error", "ok"]
    assert json.loads(provider.calls[1]["input"][-1]["output"])["error"]["code"] == "invalid_arguments"


def test_missing_key_is_persisted_without_calling_provider(settings):
    provider = ScriptedProvider(answer("must not be used"))
    service, chat_id = new_chat(replace(settings, api_key=None), provider)
    result = service.send_message(chat_id, "Hello")
    assert result["assistant_message"]["status"] == "error"
    assert "OPENAI_API_KEY" in result["assistant_message"]["content"]
    assert provider.calls == []
    assert len(service.store.get_conversation(chat_id)["messages"]) == 2


def test_upstream_error_is_safe_and_keeps_completed_tool_trace(settings, search_args):
    provider = ScriptedProvider(calls(("search_flights", search_args, "search")), RuntimeError("Authorization: Bearer sk-secret-sensitive"))
    service, chat_id = new_chat(settings, provider)
    result = service.send_message(chat_id, "JFK-LHR on 2026-11-16")
    assert result["assistant_message"]["status"] == "error"
    assert len(result["assistant_message"]["tool_events"]) == 1
    assert "sk-secret" not in json.dumps(service.store.get_conversation(chat_id))
    next_provider = ScriptedProvider(answer("Which flight would you like to search?"))
    resumed = ChatService(settings, provider=next_provider)
    resumed.send_message(chat_id, "Try again")
    resumed_input = next_provider.calls[0]["input"]
    assert resumed_input[-1] == {"role": "user", "content": "Try again"}
    recovered_outputs = [item for item in resumed_input if item.get("type") == "function_call_output"]
    assert json.loads(recovered_outputs[0]["output"]) == result["assistant_message"]["tool_events"][0]["result"]


def test_model_request_limit_preserves_events_without_unmatched_context(settings, search_args):
    provider = ScriptedProvider(*(calls(("search_flights", search_args, f"search-{index}")) for index in range(5)))
    service, chat_id = new_chat(settings, provider)
    result = service.send_message(chat_id, "JFK-LHR on 2026-11-16")
    assistant = result["assistant_message"]
    assert assistant["status"] == "error"
    assert len(provider.calls) == len(assistant["tool_events"]) == 5
    history = service.store.provider_history(chat_id, max_turns=12, max_chars=48000)
    assert len([item for item in history if item.get("type") == "function_call"]) == 5
    assert len([item for item in history if item.get("type") == "function_call_output"]) == 5
    assert history[-1] == {"role": "assistant", "content": assistant["content"]}


def test_tool_execution_limit_is_enforced_even_if_provider_batches(settings, search_args):
    batch = [("search_flights", search_args, f"call-{index}") for index in range(9)]
    service, chat_id = new_chat(settings, ScriptedProvider(calls(*batch)))
    result = service.send_message(chat_id, "JFK-LHR 2026-11-16")
    assert result["assistant_message"]["status"] == "error"
    assert result["assistant_message"]["tool_events"] == []


def test_eight_tools_can_complete_within_budget(settings, search_args):
    provider = ScriptedProvider(calls(*[("search_flights", search_args, f"a-{index}") for index in range(4)]), calls(*[("search_flights", search_args, f"b-{index}") for index in range(4)]), answer("Simulated search complete."))
    service, chat_id = new_chat(settings, provider)
    result = service.send_message(chat_id, "JFK-LHR 2026-11-16")
    assert result["assistant_message"]["status"] == "complete"
    assert len(result["assistant_message"]["tool_events"]) == 8


@pytest.mark.parametrize("response", [SimpleNamespace(status="incomplete", output=[]), SimpleNamespace(status="completed", output=[]), calls(("search_flights", "{}", ""))])
def test_incomplete_empty_and_invalid_protocol_responses_are_persisted_errors(settings, response):
    service, chat_id = new_chat(settings, ScriptedProvider(response))
    result = service.send_message(chat_id, "Search")
    assert result["assistant_message"]["status"] == "error"
    history = service.store.provider_history(chat_id, max_turns=12, max_chars=48000)
    assert [item["role"] for item in history] == ["user", "assistant"]


def test_context_prunes_whole_old_turns_and_bounds_every_request(settings):
    service, chat_id = new_chat(settings, ScriptedProvider(answer("Which date?")))
    for index in range(15):
        token, _ = service.store.begin_turn(chat_id, f"old-{index}")
        transcript = [{"role": "user", "content": f"old-{index}"}, {"type": "function_call", "call_id": f"old-call-{index}", "name": "get_flight_details", "arguments": '{"flight_id":"SIM-JFK-LHR-20261116-1"}'}, {"type": "function_call_output", "call_id": f"old-call-{index}", "output": json.dumps({"padding": "x" * 6000})}, {"role": "assistant", "content": "Simulated details."}]
        service.store.finish_turn(chat_id, token, "Simulated details.", "complete", [], transcript)
    service.send_message(chat_id, "What about tomorrow?")
    request = service._provider.calls[0]
    assert len(request["instructions"]) + len(json.dumps(request["tools"])) + len(json.dumps(request["input"])) <= MAX_CONTEXT_CHARS
    call_ids = {item["call_id"] for item in request["input"] if item.get("type") == "function_call"}
    output_ids = {item["call_id"] for item in request["input"] if item.get("type") == "function_call_output"}
    assert call_ids == output_ids
    assert 0 < len(call_ids) < 12
    assert "old-call-0" not in call_ids


def test_busy_lease_is_shared_across_store_instances(settings):
    first = Store(settings.database_path)
    second = Store(settings.database_path)
    chat_id = first.create_conversation()["id"]
    token, _ = first.begin_turn(chat_id, "First message")
    with pytest.raises(ConversationBusy):
        second.begin_turn(chat_id, "Rejected message")
    assert len(first.get_conversation(chat_id)["messages"]) == 1
    first.release_turn(chat_id, token)
    next_token, _ = second.begin_turn(chat_id, "Accepted message")
    second.release_turn(chat_id, next_token)


@pytest.mark.parametrize("content", ["", "   ", "x" * 4001, 123])
def test_invalid_messages_do_not_write_history(settings, content):
    service, chat_id = new_chat(settings, ScriptedProvider())
    with pytest.raises(ValueError):
        service.send_message(chat_id, content)
    assert service.store.get_conversation(chat_id)["messages"] == []


def test_private_reasoning_is_neither_displayed_nor_persisted(settings):
    response = SimpleNamespace(status="completed", output=[
        SimpleNamespace(type="reasoning", summary=[SimpleNamespace(text="private-test-reasoning")]),
        SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="What departure date would you like?")]),
    ])
    service, chat_id = new_chat(settings, ScriptedProvider(response))
    result = service.send_message(chat_id, "JFK to LHR")
    assert result["assistant_message"]["status"] == "complete"
    assert "private-test-reasoning" not in json.dumps(service.store.get_conversation(chat_id))
    assert "private-test-reasoning" not in json.dumps(service.store.provider_history(chat_id, max_turns=12, max_chars=48000))


def test_network_failure_is_safe_and_the_conversation_can_continue(settings):
    import httpx
    from openai import APIConnectionError

    failure = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    provider = ScriptedProvider(failure, answer("Which departure date would you like?"))
    service, chat_id = new_chat(settings, provider)
    failed = service.send_message(chat_id, "JFK to LHR")
    assert failed["assistant_message"]["status"] == "error"
    recovered = service.send_message(chat_id, "November 16, 2026")
    assert recovered["assistant_message"]["status"] == "complete"
    assert len(service.store.get_conversation(chat_id)["messages"]) == 4


@pytest.mark.parametrize("statuses", [(503, 429, 200), (503, 503, 503)])
def test_real_sdk_recovers_or_stops_after_two_retries(settings, monkeypatch, statuses):
    import httpx

    attempts = []

    def handle_request(_transport, request):
        status = statuses[len(attempts)]
        attempts.append(status)
        if status != 200:
            return httpx.Response(status, request=request, headers={"retry-after": "0"}, json={"error": {"message": "Temporary test outage", "type": "server_error"}})
        return httpx.Response(200, request=request, json={
            "id": "resp_retry_test", "object": "response", "created_at": 0,
            "status": "completed", "model": settings.model,
            "output": [{"id": "msg_retry_test", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Which departure date?", "annotations": []}]}],
        })

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr("openai._base_client.time.sleep", lambda _seconds: None)
    service = ChatService(settings)
    chat_id = service.store.create_conversation()["id"]
    try:
        result = service.send_message(chat_id, "JFK to LHR")
    finally:
        service._provider.close()
    assert attempts == list(statuses)
    expected_status = "complete" if statuses[-1] == 200 else "error"
    assert result["assistant_message"]["status"] == expected_status
    assert service.store.get_conversation(chat_id)["messages"][-1]["status"] == expected_status
