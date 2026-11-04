"""Small, server-side environment configuration."""
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str | None = None
    model: str = "gpt-4.1-mini"
    db_path: str = "data/flights.sqlite3"
    simulate_failure: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini",
            db_path=os.getenv("FLIGHT_DB_PATH", "data/flights.sqlite3"),
            simulate_failure=os.getenv("FLIGHT_SIMULATE_FAILURE") == "1",
        )

    @property
    def database_path(self) -> Path:
        return Path(self.db_path).expanduser()
