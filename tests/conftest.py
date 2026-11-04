from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from flight_assistant.config import Settings


class ScriptedProvider:
    """A test-only Responses API double; production always uses the real SDK."""
    def __init__(self, *responses):
        self.script = list(responses)
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if not self.script:
            raise AssertionError("Unexpected extra provider request")
        response = self.script.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def answer(text):
    return SimpleNamespace(status="completed", output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text)])])


def calls(*items):
    return SimpleNamespace(status="completed", output=[SimpleNamespace(type="function_call", name=name, arguments=arguments if isinstance(arguments, str) else json.dumps(arguments), call_id=call_id) for name, arguments, call_id in items])


@pytest.fixture
def settings(tmp_path):
    return Settings(api_key="test-only-not-a-real-key", db_path=str(tmp_path / "history.sqlite3"))


@pytest.fixture
def search_args():
    return {"origin": "JFK", "destination": "LHR", "date": "2026-11-16", "max_price": None, "nonstop": None}
