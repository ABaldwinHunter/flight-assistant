from datetime import datetime, timedelta, timezone
from itertools import permutations
import json
import os
import subprocess
import sys

import pytest

from flight_assistant.tools import execute_tool

AIRPORT_CODES = ("JFK", "LHR", "CDG", "SFO", "ATL", "LAX", "ORD", "BOS", "MIA")


def search(arguments, **kwargs):
    return execute_tool("search_flights", json.dumps(arguments), **kwargs)


def test_search_orders_and_filters_inclusively(search_args):
    _, result, status = search(search_args)
    assert status == "ok"
    assert result["data_source"] == "simulated"
    assert result["currency"] == "USD"
    flights = result["flights"]
    assert [flight["price"] for flight in flights] == sorted(flight["price"] for flight in flights)
    cheapest_nonstop = next(flight for flight in flights if flight["stops"] == 0)
    _, filtered, status = search({**search_args, "max_price": cheapest_nonstop["price"], "nonstop": True})
    assert status == "ok"
    assert [flight["flight_id"] for flight in filtered["flights"]] == [cheapest_nonstop["flight_id"]]
    _, below_minimum, _ = search({**search_args, "max_price": flights[0]["price"] - 1})
    assert below_minimum["flights"] == []


def test_empty_search_is_success_not_failure(search_args):
    for change in ({"max_price": 1}, {"max_price": 0, "nonstop": True}):
        _, result, status = search({**search_args, **change})
        assert status == "ok"
        assert result["flights"] == []
        assert result["count"] == 0
        assert "error" not in result


def test_details_supply_baggage_and_are_not_mutable_catalogue_aliases():
    _, searched, _ = search({"origin": "ATL", "destination": "LAX", "date": "2028-02-29", "max_price": None, "nonstop": None})
    summary = searched["flights"][0]
    arguments = json.dumps({"flight_id": summary["flight_id"]})
    _, result, status = execute_tool("get_flight_details", arguments)
    assert status == "ok"
    assert {key: value for key, value in result["flight"].items() if key not in {"baggage", "fare_rules"}} == summary
    baggage = result["flight"]["baggage"].copy()
    result["flight"]["baggage"]["checked"] = "tampered"
    _, fresh, _ = execute_tool("get_flight_details", arguments)
    assert fresh["flight"]["baggage"] == baggage
    _, missing, status = execute_tool("get_flight_details", '{"flight_id":"not-real"}')
    assert status == "error"
    assert missing["error"]["code"] == "flight_not_found"


@pytest.mark.parametrize("changes", [
    {"origin": "ABC"}, {"origin": ["JFK"]}, {"destination": "JFK"},
    {"date": "2026-02-30"}, {"date": "20261116"}, {"date": None},
    {"max_price": True}, {"max_price": -1}, {"max_price": "600"},
    {"max_price": 10**400},
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


@pytest.mark.parametrize("origin,destination", permutations(AIRPORT_CODES, 2))
@pytest.mark.parametrize("departure_date", ["2027-01-15", "2027-07-15", "2028-02-29"])
def test_all_supported_routes_and_dates_have_consistent_flights(origin, destination, departure_date):
    query = {"origin": origin, "destination": destination, "date": departure_date, "max_price": None, "nonstop": None}
    _, result, status = search(query)
    assert status == "ok"
    flights = result["flights"]
    assert {flight["stops"] for flight in flights} == {0, 1}
    assert len({flight["flight_id"] for flight in flights}) == len(flights)
    for flight in flights:
        assert (flight["origin"], flight["destination"], flight["date"]) == (origin, destination, departure_date)
        departure = datetime.fromisoformat(flight["departure"])
        arrival = datetime.fromisoformat(flight["arrival"])
        assert departure.date().isoformat() == departure_date
        assert departure.tzinfo is not None and arrival.tzinfo is not None
        elapsed = arrival.astimezone(timezone.utc) - departure.astimezone(timezone.utc)
        assert elapsed == timedelta(minutes=flight["duration_minutes"])
        assert flight["currency"] == "USD" and flight["price"] > 1
        if flight["stops"]:
            assert flight["connection"] in set(AIRPORT_CODES) - {origin, destination}
        _, details, status = execute_tool("get_flight_details", json.dumps({"flight_id": flight["flight_id"]}))
        assert status == "ok"
        assert details["flight"]["departure"] == flight["departure"]


def test_search_and_details_are_stable_in_a_fresh_process(search_args):
    query = {**search_args, "origin": "BOS", "destination": "MIA", "date": "2031-12-31"}
    _, expected, _ = search(query)
    flight_id = expected["flights"][0]["flight_id"]
    _, expected_details, _ = execute_tool("get_flight_details", json.dumps({"flight_id": flight_id}))
    script = (
        "import json; from flight_assistant.tools import execute_tool; "
        f"print(json.dumps([execute_tool('search_flights', {json.dumps(query)!r})[1], "
        f"execute_tool('get_flight_details', {json.dumps({'flight_id': flight_id})!r})[1]]))"
    )
    output = subprocess.check_output([sys.executable, "-c", script], text=True, env={**os.environ, "PYTHONHASHSEED": "123"})
    assert json.loads(output) == [expected, expected_details]


@pytest.mark.parametrize("departure_date,offset", [("2027-01-15", -5), ("2027-07-15", -4)])
def test_local_offsets_and_overnight_arrivals(search_args, departure_date, offset):
    _, result, _ = search({**search_args, "date": departure_date, "nonstop": True})
    for flight in result["flights"]:
        assert datetime.fromisoformat(flight["departure"]).utcoffset() == timedelta(hours=offset)
    latest = max(result["flights"], key=lambda flight: flight["departure"])
    assert datetime.fromisoformat(latest["arrival"]).date() > datetime.fromisoformat(latest["departure"]).date()


@pytest.mark.parametrize("flight_id", [
    "SIM-ATL-101", "SIM-ATL-LAX-20280230-1", "SIM-ATL-LAX-20280229-9",
    "SIM-ATL-ATL-20280229-1", "SIM-ABC-LAX-20280229-1",
])
def test_unresolvable_ids_are_structured_errors(flight_id):
    _, result, status = execute_tool("get_flight_details", json.dumps({"flight_id": flight_id}))
    assert status == "error"
    assert result["error"]["code"] == "flight_not_found"
