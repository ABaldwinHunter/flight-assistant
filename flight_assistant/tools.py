"""An allowlisted, deterministic fictional flight catalogue. No booking or live data."""
from copy import deepcopy
from datetime import date
import json
import math

DEMO_DATE = "2026-11-16"
DEMO_ROUTES = ["JFK → LHR", "JFK → CDG", "SFO → JFK"]
AIRPORTS = {"JFK", "LHR", "CDG", "SFO"}

# The IDs are permanent fixture identifiers, not real flight numbers.
FLIGHTS = (
    {
        "flight_id": "SIM-ATL-101", "airline": "Atlantic Lantern (fictional)",
        "origin": "JFK", "destination": "LHR", "date": DEMO_DATE,
        "departure": "2026-11-16T19:00:00-05:00", "arrival": "2026-11-17T07:00:00+00:00",
        "duration_minutes": 420, "stops": 0, "price": 545, "currency": "USD",
        "fare": "Economy Light", "baggage": {"personal_item": "1 included", "carry_on": "1 bag up to 8 kg included", "checked": "Not included; first bag USD 60 each way"},
        "fare_rules": {"changes": "USD 100 plus fare difference", "refunds": "Non-refundable"},
    },
    {
        "flight_id": "SIM-ATL-102", "airline": "Atlantic Lantern (fictional)",
        "origin": "JFK", "destination": "LHR", "date": DEMO_DATE,
        "departure": "2026-11-16T21:30:00-05:00", "arrival": "2026-11-17T09:40:00+00:00",
        "duration_minutes": 430, "stops": 0, "price": 675, "currency": "USD",
        "fare": "Economy Standard", "baggage": {"personal_item": "1 included", "carry_on": "1 bag up to 10 kg included", "checked": "1 bag up to 23 kg included"},
        "fare_rules": {"changes": "No change fee; fare difference applies", "refunds": "Non-refundable"},
    },
    {
        "flight_id": "SIM-BRG-201", "airline": "Bluebridge Air (fictional)",
        "origin": "JFK", "destination": "LHR", "date": DEMO_DATE,
        "departure": "2026-11-16T16:00:00-05:00", "arrival": "2026-11-17T08:45:00+00:00",
        "duration_minutes": 705, "stops": 1, "connection": "CDG",
        "price": 430, "currency": "USD", "fare": "Economy Saver",
        "baggage": {"personal_item": "1 included", "carry_on": "1 bag up to 8 kg included", "checked": "Not included; first bag USD 70 each way"},
        "fare_rules": {"changes": "Not permitted", "refunds": "Non-refundable"},
    },
    {
        "flight_id": "SIM-BRG-202", "airline": "Bluebridge Air (fictional)",
        "origin": "JFK", "destination": "CDG", "date": DEMO_DATE,
        "departure": "2026-11-16T18:30:00-05:00", "arrival": "2026-11-17T08:00:00+01:00",
        "duration_minutes": 450, "stops": 0, "price": 510, "currency": "USD",
        "fare": "Economy Standard", "baggage": {"personal_item": "1 included", "carry_on": "1 bag up to 8 kg included", "checked": "1 bag up to 23 kg included"},
        "fare_rules": {"changes": "USD 75 plus fare difference", "refunds": "Non-refundable"},
    },
    {
        "flight_id": "SIM-COA-301", "airline": "Coastal Skylines (fictional)",
        "origin": "SFO", "destination": "JFK", "date": DEMO_DATE,
        "departure": "2026-11-16T08:00:00-08:00", "arrival": "2026-11-16T16:30:00-05:00",
        "duration_minutes": 330, "stops": 0, "price": 245, "currency": "USD",
        "fare": "Economy Basic", "baggage": {"personal_item": "1 included", "carry_on": "1 bag up to 10 kg included", "checked": "Not included; first bag USD 35 each way"},
        "fare_rules": {"changes": "Not permitted", "refunds": "Non-refundable"},
    },
    {
        "flight_id": "SIM-COA-302", "airline": "Coastal Skylines (fictional)",
        "origin": "SFO", "destination": "JFK", "date": DEMO_DATE,
        "departure": "2026-11-16T22:00:00-08:00", "arrival": "2026-11-17T06:20:00-05:00",
        "duration_minutes": 320, "stops": 0, "price": 315, "currency": "USD",
        "fare": "Economy Standard", "baggage": {"personal_item": "1 included", "carry_on": "1 bag up to 10 kg included", "checked": "1 bag up to 23 kg included"},
        "fare_rules": {"changes": "No change fee; fare difference applies", "refunds": "Non-refundable"},
    },
    {
        "flight_id": "SIM-ATL-103", "airline": "Atlantic Lantern (fictional)",
        "origin": "JFK", "destination": "LHR", "date": "2026-11-17",
        "departure": "2026-11-17T19:00:00-05:00", "arrival": "2026-11-18T07:00:00+00:00",
        "duration_minutes": 420, "stops": 0, "price": 565, "currency": "USD",
        "fare": "Economy Light", "baggage": {"personal_item": "1 included", "carry_on": "1 bag up to 8 kg included", "checked": "Not included; first bag USD 60 each way"},
        "fare_rules": {"changes": "USD 100 plus fare difference", "refunds": "Non-refundable"},
    },
)

TOOL_SCHEMAS = [
    {
        "type": "function", "name": "search_flights",
        "description": "Search fictional simulated flights only. Requires an explicit route and calendar departure date. Prices are one adult, one way, USD, including simulated taxes.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "origin": {"type": "string", "enum": sorted(AIRPORTS)},
                "destination": {"type": "string", "enum": sorted(AIRPORTS)},
                "date": {"type": "string", "description": "Departure date YYYY-MM-DD, as specified by the user."},
                "max_price": {"type": ["number", "null"], "description": "Maximum one-way USD price, or null for no price filter."},
                "nonstop": {"type": ["boolean", "null"], "description": "True for nonstop only; false or null includes connecting flights."},
            },
            "required": ["origin", "destination", "date", "max_price", "nonstop"],
        },
    },
    {
        "type": "function", "name": "get_flight_details",
        "description": "Retrieve a simulated flight by stable fixture ID, including baggage and fare rules.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"flight_id": {"type": "string"}},
            "required": ["flight_id"],
        },
    },
]


class ToolInputError(ValueError):
    pass


def _validate(name: str, arguments: object) -> dict:
    if not isinstance(arguments, dict):
        raise ToolInputError("Arguments must be a JSON object.")
    if name == "search_flights":
        if set(arguments) != {"origin", "destination", "date", "max_price", "nonstop"}:
            raise ToolInputError("Provide origin, destination, date, max_price and nonstop only; use null for optional filters.")
        for field in ("origin", "destination"):
            if not isinstance(arguments[field], str) or arguments[field] not in AIRPORTS:
                raise ToolInputError(f"{field} must be JFK, LHR, CDG or SFO.")
        if arguments["origin"] == arguments["destination"]:
            raise ToolInputError("Origin and destination must differ.")
        value = arguments["date"]
        if not isinstance(value, str):
            raise ToolInputError("date must be YYYY-MM-DD.")
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError:
            raise ToolInputError("date must be a valid YYYY-MM-DD calendar date.") from None
        price = arguments["max_price"]
        if price is not None and (type(price) not in (int, float) or not math.isfinite(price) or price < 0):
            raise ToolInputError("max_price must be a finite non-negative USD number or null.")
        if arguments["nonstop"] is not None and type(arguments["nonstop"]) is not bool:
            raise ToolInputError("nonstop must be a boolean or null.")
    elif name == "get_flight_details":
        if set(arguments) != {"flight_id"} or not isinstance(arguments["flight_id"], str) or not 1 <= len(arguments["flight_id"]) <= 80:
            raise ToolInputError("Provide only a nonempty flight_id string of at most 80 characters.")
    else:
        raise ToolInputError("Unknown tool. Only search_flights and get_flight_details are available.")
    return arguments


def execute_tool(name: str, raw_arguments: str, *, simulate_failure: bool = False) -> tuple[object, dict, str]:
    """Return independently validated arguments, a safe result, and event status."""
    arguments: object = raw_arguments[:8000] if isinstance(raw_arguments, str) else ""
    try:
        if not isinstance(raw_arguments, str) or len(raw_arguments) > 8000:
            raise ToolInputError("Tool arguments must be a JSON string of at most 8000 characters.")
        try:
            arguments = json.loads(raw_arguments, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (ValueError, RecursionError):
            raise ToolInputError("Arguments must contain valid finite JSON.") from None
        args = _validate(name, arguments)
    except ToolInputError as error:
        return arguments, {"data_source": "simulated", "error": {"code": "invalid_arguments", "message": str(error)}}, "error"
    if simulate_failure:
        return arguments, {"data_source": "simulated", "error": {"code": "simulated_failure", "message": "Simulated flight tools are intentionally unavailable (FLIGHT_SIMULATE_FAILURE=1). No flight data was retrieved."}}, "error"
    if name == "search_flights":
        matches = [flight for flight in FLIGHTS if flight["origin"] == args["origin"] and flight["destination"] == args["destination"] and flight["date"] == args["date"] and (args["max_price"] is None or flight["price"] <= args["max_price"]) and (args["nonstop"] is not True or flight["stops"] == 0)]
        matches.sort(key=lambda flight: (flight["price"], flight["departure"], flight["flight_id"]))
        summaries = [{key: value for key, value in flight.items() if key not in {"baggage", "fare_rules"}} for flight in matches]
        return arguments, {"data_source": "simulated", "currency": "USD", "price_basis": "One adult, one way, simulated taxes included", "query": args, "flights": deepcopy(summaries), "count": len(summaries)}, "ok"
    flight = next((flight for flight in FLIGHTS if flight["flight_id"] == args["flight_id"]), None)
    if flight is None:
        return arguments, {"data_source": "simulated", "error": {"code": "flight_not_found", "message": "No simulated flight has that ID. Use an ID returned by search_flights."}}, "error"
    return arguments, {"data_source": "simulated", "flight": deepcopy(flight)}, "ok"
