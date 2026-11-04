from flight_assistant import cli
from flight_assistant.service import ChatService
from conftest import ScriptedProvider, answer, calls


def test_terminal_trace_new_chat_and_resume_use_saved_history(settings, search_args, monkeypatch, capsys):
    provider = ScriptedProvider(calls(("search_flights", search_args, "search")), answer("Simulated SIM-ATL-101 costs USD 545."))
    service = ChatService(settings, provider=provider)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "ChatService", lambda _settings: service)
    inputs = iter(["JFK to LHR on 2026-11-16", "/new", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    assert cli.main(["--trace"]) == 0
    output = capsys.readouterr().out
    assert "SIMULATED" in output
    assert "search_flights" in output
    assert "SIM-ATL-101" in output
    conversations = service.store.list_conversations()
    assert len(conversations) == 2
    completed = next(chat for chat in conversations if service.store.get_conversation(chat["id"])["messages"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "/quit")
    assert cli.main(["--trace", "--conversation", completed["id"]]) == 0
    resumed_output = capsys.readouterr().out
    assert "search_flights" in resumed_output
    assert "SIM-ATL-101" in resumed_output
    assert len(provider.calls) == 2
    assert cli.main(["--list"]) == 0
    assert completed["id"] in capsys.readouterr().out


def test_unknown_conversation_is_safe_terminal_error(settings, monkeypatch, capsys):
    service = ChatService(settings, provider=ScriptedProvider())
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "ChatService", lambda _settings: service)
    assert cli.main(["--conversation", "missing"]) == 1
