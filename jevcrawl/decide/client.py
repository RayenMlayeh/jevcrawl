"""Async client for the Jev decision API (``POST /v1/systemone``), with retries.

Written on ``httpx`` rather than ``typesafe-sdk`` so it can be mocked in tests and pointed at any
Jev-compatible endpoint.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from jevcrawl import REPO_URL, __version__
from jevcrawl.config import Config
from jevcrawl.log import get_logger

log = get_logger("decide.client")

SYSTEM_ONE_PATH = "/v1/systemone"
MODELS_PATH = "/v1/models"
REQUEST_TIMEOUT = 30.0
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (0.5, 1.0, 2.0, 4.0, 8.0)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
MAX_RETRY_AFTER = 30.0
USER_AGENT = f"jevcrawl/{__version__} (+{REPO_URL})"

Sleep = Callable[[float], Awaitable[None]]


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)  # looked up at call time, so tests can patch asyncio.sleep


class JevError(Exception):
    """Base class for Jev API failures."""


class JevAuthError(JevError):
    """The API key is missing or was rejected (HTTP 401/403)."""


class JevRequestError(JevError):
    """Jev rejected the request (HTTP 4xx other than 401/403 and 429)."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class JevUnavailableError(JevError):
    """Retries were exhausted on 429/5xx, timeouts or connection errors."""


class JevResponseError(JevError):
    """Jev answered 200 with a body we cannot use."""


@dataclass(frozen=True)
class Evaluation:
    """Parsed answer to one ``/v1/systemone`` request."""

    model: str
    nouls: dict[str, float]
    input_tokens: int
    output_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER) if value is not None else None
    except ValueError:
        return None  # an HTTP date: fall back to our own backoff


class DecisionClient:
    """Talks to ``POST /v1/systemone`` and ``GET /v1/models``."""

    def __init__(
        self,
        config: Config,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = _default_sleep,
        rng: Callable[[], float] = random.random,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._config = config
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(http2=True, timeout=timeout)
        self._sleep = sleep
        self._rng = rng
        self._timeout = timeout

    async def __aenter__(self) -> DecisionClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if this object created it."""
        if self._owns_http:
            await self._http.aclose()

    async def evaluate(self, body: dict[str, Any]) -> Evaluation:
        """Send one request and return its parsed noul answers and token usage."""
        data = await self._send("POST", SYSTEM_ONE_PATH, body)
        return self._parse(data)

    async def list_models(self) -> list[dict[str, Any]]:
        """``GET /v1/models``: the model names this account can use."""
        data = await self._send("GET", MODELS_PATH, None)
        models = data.get("models")
        if not isinstance(models, list):
            raise JevResponseError("GET /v1/models returned no 'models' list")
        return [m for m in models if isinstance(m, dict)]

    def _headers(self) -> dict[str, str]:
        key = self._config.api_key
        if not key:
            raise JevAuthError("No Jev API key. Set the TYPESAFE_API_KEY environment variable.")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def _send(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        url = f"{self._config.base_url.rstrip('/')}{path}"
        headers = self._headers()
        content = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        last = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            retry_after: float | None = None
            try:
                response = await self._http.request(
                    method, url, content=content, headers=headers, timeout=self._timeout
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = f"{type(exc).__name__}"
            else:
                if response.status_code == 200:
                    return self._json(response)
                self._raise_if_fatal(response)
                last = f"HTTP {response.status_code}"
                retry_after = _retry_after(response)
            if attempt + 1 < MAX_ATTEMPTS:
                delay = self._delay(attempt, retry_after)
                log.info("%s failed (%s); retry %d in %.2fs", path, last, attempt + 1, delay)
                await self._sleep(delay)
        raise JevUnavailableError(f"{path} still failing after {MAX_ATTEMPTS} attempts ({last})")

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        jitter = 0.75 + 0.5 * self._rng()  # 0.75x .. 1.25x
        return BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)] * jitter

    @staticmethod
    def _raise_if_fatal(response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            raise JevAuthError(f"The API key was rejected (HTTP {status}). Check TYPESAFE_API_KEY.")
        if status in RETRY_STATUSES:
            return
        body = response.text[:2000]
        log.error("request rejected with HTTP %d: %s", status, body)
        raise JevRequestError(f"request rejected (HTTP {status}): {body}", status, body)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise JevResponseError("response body is not JSON") from exc
        if not isinstance(data, dict):
            raise JevResponseError("response body is not a JSON object")
        return data

    @staticmethod
    def _parse(data: dict[str, Any]) -> Evaluation:
        try:
            answers = data["answers"]
            model = str(data["model"])
            usage = data.get("usage") or {}
            nouls = {
                qid: min(1.0, max(0.0, float(a["noul"])))
                for qid, a in answers.items()
                if isinstance(a, dict) and a.get("type") == "noul"
            }
            return Evaluation(
                model,
                nouls,
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
                data,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise JevResponseError(f"unexpected response shape: {exc!r}") from exc
