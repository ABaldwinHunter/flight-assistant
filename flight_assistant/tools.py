"""An allowlisted, deterministic fictional flight catalogue. No booking or live data."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo

DEMO_DATE = "2026-11-16"
DEMO_ROUTES = ["JFK → LHR", "JFK → CDG", "SFO → JFK"]
# Time zone and coordinates are used only to make fictional schedules plausible.
AIRPORTS = {
    "JFK": ("America/New_York", 40.64, -73.78),
    "LHR": ("Europe/London", 51.47, -0.45),
    "CDG": ("Europe/Paris", 49.01, 2.55),
    "SFO": ("America/Los_Angeles", 37.62, -122.38),
    "ATL": ("America/New_York", 33.64, -84.43),
    "LAX": ("America/Los_Angeles", 33.94, -118.41),
    "ORD": ("America/Chicago", 41.98, -87.90),
    "BOS": ("America/New_York", 42.37, -71.01),
    "MIA": ("America/New_York", 25.80, -80.29),
}


def _generate_flights(origin: str, destination: str, departure_date: str) -> list[dict]:
    """Three fictional options per route/date; SHA-256 is stable across processes."""
    seed = sha256(f"{origin}|{destination}|{departure_date}".encode()).digest()
    origin_zone, lat1, lon1 = AIRPORTS[origin]
    destination_zone, lat2, lon2 = AIRPORTS[destination]
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    haversine = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    distance_km = 12742 * math.asin(math.sqrt(min(1.0, haversine)))
    base_duration = 45 + math.ceil(distance_km / 800 * 12) * 5
    base_price = 80 + int(distance_km / 25) + seed[0] % 100
    day = date.fromisoformat(departure_date)
    connections = sorted(set(AIRPORTS) - {origin, destination})
    flights = []
    for index, (hour, price_extra, airline, fare) in enumerate((
        (8, 50, "Atlantic Lantern (fictional)", "Economy Light"),
        (15, 150, "Coastal Skylines (fictional)", "Economy Standard"),
        (6, 0, "Bluebridge Air (fictional)", "Economy Saver"),
    )):
        departure = datetime(day.year, day.month, day.day, hour + seed[index + 1] % 3, seed[index + 4] % 4 * 15, tzinfo=ZoneInfo(origin_zone))
        stops = int(index == 2)
        duration = base_duration + index * 10 + stops * 150
        arrival = (departure.astimezone(timezone.utc) + timedelta(minutes=duration)).astimezone(ZoneInfo(destination_zone))
        flight = {
            "flight_id": f"SIM-{origin}-{destination}-{departure_date.replace('-', '')}-{index + 1}",
            "airline": airline, "origin": origin, "destination": destination,
            "date": departure_date, "departure": departure.isoformat(), "arrival": arrival.isoformat(),
            "duration_minutes": duration, "stops": stops,
            "price": base_price + price_extra, "currency": "USD", "fare": fare,
            "baggage": {
                "personal_item": "1 included",
                "carry_on": "1 bag up to 8 kg included",
                "checked": "1 bag up to 23 kg included" if index == 1 else "Not included; first bag USD 60 each way",
            },
            "fare_rules": {
                "changes": "No change fee; fare difference applies" if index == 1 else "USD 100 plus fare difference",
                "refunds": "Non-refundable",
            },
        }
        if stops:
            flight["connection"] = connections[seed[7] % len(connections)]
        flights.append(flight)
    return flights


def _find_flight(flight_id: str) -> dict | None:
    try:
        prefix, origin, destination, day, _number = flight_id.split("-")
        if prefix != "SIM" or origin not in AIRPORTS or destination not in AIRPORTS or origin == destination:
            return None
        departure_date = date.fromisoformat(day).isoformat()
    except ValueError:
        return None
    return next((flight for flight in _generate_flights(origin, destination, departure_date) if flight["flight_id"] == flight_id), None)

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
        "description": "Retrieve a simulated flight by the stable ID returned by search_flights, including baggage and fare rules.",
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
                raise ToolInputError(f"{field} must be one of: {', '.join(sorted(AIRPORTS))}.")
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
        if price is not None:
            try:
                valid_price = type(price) in (int, float) and math.isfinite(price) and price >= 0
            except OverflowError:
                valid_price = False
            if not valid_price:
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
            decoded = json.loads(raw_arguments, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            json.dumps(decoded, allow_nan=False)
            arguments = decoded
        except (ValueError, RecursionError):
            raise ToolInputError("Arguments must contain valid finite JSON.") from None
        args = _validate(name, arguments)
    except ToolInputError as error:
        return arguments, {"data_source": "simulated", "error": {"code": "invalid_arguments", "message": str(error)}}, "error"
    if simulate_failure:
        return arguments, {"data_source": "simulated", "error": {"code": "simulated_failure", "message": "Simulated flight tools are intentionally unavailable (FLIGHT_SIMULATE_FAILURE=1). No flight data was retrieved."}}, "error"
    if name == "search_flights":
        matches = [flight for flight in _generate_flights(args["origin"], args["destination"], args["date"]) if (args["max_price"] is None or flight["price"] <= args["max_price"]) and (args["nonstop"] is not True or flight["stops"] == 0)]
        matches.sort(key=lambda flight: (flight["price"], flight["departure"], flight["flight_id"]))
        summaries = [{key: value for key, value in flight.items() if key not in {"baggage", "fare_rules"}} for flight in matches]
        return arguments, {"data_source": "simulated", "currency": "USD", "price_basis": "One adult, one way, simulated taxes included", "query": args, "flights": summaries, "count": len(summaries)}, "ok"
    flight = _find_flight(args["flight_id"])
    if flight is None:
        return arguments, {"data_source": "simulated", "error": {"code": "flight_not_found", "message": "No simulated flight has that ID. Use an ID returned by search_flights."}}, "error"
    return arguments, {"data_source": "simulated", "flight": flight}, "ok"
