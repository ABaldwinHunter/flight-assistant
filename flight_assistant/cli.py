"""Optional terminal client using exactly the same service, tools and SQLite history."""
import argparse
import json
import sqlite3

from .config import Settings
from .service import ChatService
from .store import ConversationBusy, ConversationNotFound


def _print_message(message: dict, trace: bool) -> None:
    label = "You" if message["role"] == "user" else "Assistant"
    suffix = " [error]" if message["status"] == "error" else ""
    print(f"\n{label}{suffix}: {message['content']}")
    if trace:
        for event in message["tool_events"]:
            print(f"  Tool: {event['name']} [{event['status']}] ({event['duration_ms']:.3f} ms)")
            print("  Arguments: " + json.dumps(event["arguments"], ensure_ascii=False))
            print("  Result: " + json.dumps(event["result"], ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local simulated flight assistant; requires server-side OPENAI_API_KEY for model calls.")
    parser.add_argument("--trace", action="store_true", help="Show actual tool arguments, results and durations (not private reasoning).")
    parser.add_argument("--conversation", metavar="ID", help="Resume an existing SQLite conversation, including its grounded tool history.")
    parser.add_argument("--list", action="store_true", help="List saved conversation IDs and exit.")
    args = parser.parse_args(argv)
    print("SIMULATED FLIGHT DATA — fictional flights, USD prices, no booking or live availability.")
    print("Demo: 2026-11-16 · JFK → LHR / JFK → CDG / SFO → JFK")
    try:
        service = ChatService(Settings.from_env())
        if args.list:
            for conversation in service.store.list_conversations():
                print(f"{conversation['id']}  {conversation['created_at']}  {conversation['title']}")
            return 0
        if not service.settings.api_key:
            print("OpenAI is not configured. Set OPENAI_API_KEY in the shell or .env and restart; never paste it into chat.")
        if service.settings.simulate_failure:
            print("Tool-failure demonstration is enabled (FLIGHT_SIMULATE_FAILURE=1).")
        conversation = service.store.get_conversation(args.conversation) if args.conversation else service.store.create_conversation()
        print(f"Conversation: {conversation['id']}")
        for message in conversation["messages"]:
            _print_message(message, args.trace)
        print("Commands: /new starts a new saved chat; /quit exits. Use --list and --conversation ID to resume.")
        while True:
            try:
                content = input("\nYou: ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if content.strip() == "/quit":
                return 0
            if content.strip() == "/new":
                conversation = service.store.create_conversation()
                print(f"New conversation: {conversation['id']}")
                continue
            if not content.strip():
                continue
            try:
                result = service.send_message(conversation["id"], content)
                _print_message(result["assistant_message"], args.trace)
            except ValueError:
                print("Message must be nonblank and at most 4000 characters.")
            except ConversationBusy:
                print("This conversation is busy. Wait before trying again.")
            except KeyboardInterrupt:
                print("\nInterrupted; saved history remains on disk.")
                return 130
    except ConversationNotFound:
        print("Conversation not found. Use --list to see saved conversation IDs.")
        return 1
    except sqlite3.Error:
        print("Chat storage is unavailable. Check the configured FLIGHT_DB_PATH and its permissions.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
