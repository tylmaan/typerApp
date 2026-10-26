"""Reading the API key, without pulling in a dependency for it.

The key lives in the environment, or in a ``.env`` file next to the project, which is listed
in ``.gitignore``. It is never written to the database, so the file with the game data can be
copied around without leaking anything.
"""

from __future__ import annotations

import os
from pathlib import Path

API_KEY_VARIABLE = "FOOTBALL_DATA_API_KEY"

ENV_FILE_NAME = ".env"


def load_env_file(path: Path | str = ENV_FILE_NAME) -> dict[str, str]:
    """Parse a ``.env`` file: ``KEY=value`` per line, ``#`` comments, quotes stripped.

    A missing or unreadable file is not an error; the app simply has no key.
    """
    values: dict[str, str] = {}
    file = Path(path)
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def football_data_api_key(env_file: Path | str = ENV_FILE_NAME) -> str | None:
    """The key from the environment, falling back to the ``.env`` file."""
    from_environment = os.environ.get(API_KEY_VARIABLE, "").strip()
    if from_environment:
        return from_environment
    from_file = load_env_file(env_file).get(API_KEY_VARIABLE, "").strip()
    return from_file or None
