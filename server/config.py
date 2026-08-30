"""Settings, read from the environment.

Deliberately small: a database DSN, a session secret, and where the authored
content lives. Anything that needs more than an environment variable is
probably content, and content belongs in `data/`.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Settings:
    """Read once, validated when the database is first used."""

    def __init__(self) -> None:
        self.db_dsn: str = os.environ.get("DB_DSN", "")
        self.session_secret: str = os.environ.get("SESSION_SECRET", "")
        self.content_dir: Path = Path(
            os.environ.get("CONTENT_DIR", str(ROOT / "data" / "modules")))
        self.pool_min: int = int(os.environ.get("DB_POOL_MIN", "1"))
        self.pool_max: int = int(os.environ.get("DB_POOL_MAX", "8"))

    def validate(self) -> None:
        """Refuse to start half-configured.

        A missing DSN fails here with a sentence naming the variable, rather
        than as a connection error three layers down that reads like the
        database is broken.
        """
        missing = [name for name, value in
                   (("DB_DSN", self.db_dsn),
                    ("SESSION_SECRET", self.session_secret))
                   if not value]
        if missing:
            raise RuntimeError(
                "not configured: %s. Set them in the environment; see "
                "docker-compose.yml for the development values."
                % ", ".join(missing))


settings = Settings()
