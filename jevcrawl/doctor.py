"""``jevcrawl doctor``: check the API key and the connection to Jev. Never prints the key."""

from __future__ import annotations

import platform
import sys

from jevcrawl import __version__
from jevcrawl.config import Config
from jevcrawl.decide.client import DecisionClient, JevAuthError, JevError


def _line(label: str, value: object) -> None:
    print(f"{label:<11}{value}")


async def run_doctor(config: Config) -> int:
    """Print a diagnosis and return 0 if Jev answered and accepted the key, else 1."""
    _line("jevcrawl", f"{__version__} (python {platform.python_version()}, {sys.platform})")
    _line("endpoint", config.base_url)
    _line("model", config.model)
    _line("api key", f"set ({len(config.api_key)} characters)" if config.api_key else "NOT SET")
    _line("cache", config.cache_dir)
    if not config.api_key:
        print("\nproblem: no API key. Set TYPESAFE_API_KEY (https://console.typesafe.ai/).")
        return 1
    try:
        async with DecisionClient(config) as client:
            models = await client.list_models()
    except JevAuthError as exc:
        print(f"\nproblem: {exc}")
        return 1
    except JevError as exc:
        print(f"\nproblem: could not reach {config.base_url}: {exc}")
        return 1
    _line("models", ", ".join(str(m.get("name")) for m in models) or "(none listed)")
    print("\nok: Jev answered and accepted the key.")
    return 0
