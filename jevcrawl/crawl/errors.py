"""Expected failures of a crawl, with a stable code and a message that says what to do."""

from __future__ import annotations


class CrawlError(Exception):
    """An expected error. Tools return ``{"error": code, "message": ...}`` instead of raising."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        """The error payload returned to agents."""
        return {"error": self.code, "message": self.message}


AUTH_HELP = (
    "The Jev API key is missing or was rejected. Set the TYPESAFE_API_KEY environment variable "
    "(create a key at https://console.typesafe.ai/)."
)
