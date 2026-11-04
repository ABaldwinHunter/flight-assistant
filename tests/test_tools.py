from datetime import datetime
import json

import pytest

from flight_assistant.tools import FLIGHTS, execute_tool


def search(arguments, **kwargs):
    return execute_tool("search_flights", json.dumps(arguments), **kwargs)


def test_search_orders_filters_and_preserves_source(search_args):
    _, result, status = search(search_args)
    assert status == "ok"
    assert result["data_source"] == "simulated"
    assert result["currency"] == "USD"
    assert [flight["price"] for flight in result["flights"]] == [430, 545, 675]
    assert all(flight["flight_id"].startswith("SIM-") for flight in result["flights"])
    _, filtered, status = search({**search_args, "max_price": 600, "nonstop": True})
    assert status == "ok"
    assert [flight["flight_id"] for flight in filtered["flights"]] == ["SIM-ATL-101"]
    _, inclusive, _ = search({**search_args, "max_price": 545, "nonstop": False})
    assert [flight["price"] for flight in inclusive["flights"]] == [430, 545]


def test_empty_search_is_success_not_failure(search_args):
    for change in ({"max_price": 1}, {"date": "2026-12-31"}, {"origin": "LHR", "destination": "SFO"}):
        _, result, status = search({**search_args, **change})
        assert status == "ok"
        assert result["flights"] == []
        assert result["count"] == 0
        assert "error" not in result


def test_details_supply_baggage_and_are_not_mutable_catalogue_aliases():
    _, result, status = execute_tool("get_flight_details", '{"flight_id":"SIM-ATL-101"}')
    assert status == "ok"
    assert result["flight"]["baggage"]["checked"].startswith("Not included")
    assert "USD" in result["flight"]["fare_rules"]["changes"]
    result["flight"]["baggage"]["checked"] = "tampered"
    _, fresh, _ = execute_tool("get_flight_details", '{"flight_id":"SIM-ATL-101"}')
    assert fresh["flight"]["baggage"]["checked"] != "tampered"
    _, missing, status = execute_tool("get_flight_details", '{"flight_id":"not-real"}')
    assert status == "error"
    assert missing["error"]["code"] == "flight_not_found"


@pytest.mark.parametrize("changes", [
    {"origin": "ABC"}, {"origin": ["JFK"]}, {"destination": "JFK"},
    {"date": "2026-02-30"}, {"date": "20261116"}, {"date": None},
    {"max_price": True}, {"max_price": -1}, {"max_price": "600"},
    {"nonstop": 1}, {"extra": "ignored?"},
])
def test_search_independently_rejects_invalid_arguments(search_args, changes):
    _, result, status = search({**search_args, **changes})
    assert status == "error"
    assert result["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("raw", ["{", "[]", "null", '{"max_price": NaN}', '{"max_price": 1e309}', "[" * 2000 + "]" * 2000, "x" * 8001])
def test_malformed_json_is_safe_durable_error(raw):
    parsed, result, status = execute_tool("search_flights", raw)
    assert status == "error"
    json.dumps({"arguments": parsed, "result": result}, allow_nan=False)


def test_allowlist_and_explicit_failure_mode(search_args):
    _, unknown, status = execute_tool("delete_database", "{}")
    assert status == "error"
    assert unknown["error"]["code"] == "invalid_arguments"
    _, failed, status = search(search_args, simulate_failure=True)
    assert status == "error"
    assert failed["error"]["code"] == "simulated_failure"
    assert "flights" not in failed


def test_fixture_times_are_aware_and_overnight_is_explicit():
    for flight in FLIGHTS:
        departure = datetime.fromisoformat(flight["departure"])
        arrival = datetime.fromisoformat(flight["arrival"])
        assert departure.tzinfo is not None and arrival.tzinfo is not None
        assert int((arrival - departure).total_seconds() / 60) == flight["duration_minutes"]
        assert flight["currency"] == "USD"
    london = next(flight for flight in FLIGHTS if flight["flight_id"] == "SIM-ATL-101")
    assert london["arrival"][:10] > london["departure"][:10]
