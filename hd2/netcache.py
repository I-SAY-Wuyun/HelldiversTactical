"""Cached, rate-limited HTTP for the optional online features.

Only the event features talk to the network, and both sources have hard limits
(Liquipedia: <= 1 request / 2s, attribution required; lolesports: public endpoint
with a desktop-friendly UA).  Everything goes through :func:`http_cached`:

* response bodies are cached in SQLite, so "no network" still shows the last data;
* `ETag` / `If-None-Match` keeps re-fetches cheap and polite;
* 429/503 honour `Retry-After` and park the endpoint instead of hammering it;
* a single limiter keyed by host enforces the per-source minimum interval;
* a failed refresh returns the cached body with status `"stale"`, never an
  exception the caller has to guard against.
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Callable

from . import store

LOG = logging.getLogger(__name__)

USER_AGENT = "HelldiversTactical/1.0 (+desktop widget; contact: local user)"
DEFAULT_TTL = 600.0

#: (status, body, headers) -> fetcher used by tests to avoid real sockets
Fetcher = Callable[[str, dict[str, str], float], tuple[int, bytes, dict[str, str]]]


class RateLimiter:
    """Enforce a minimum interval between requests, keyed by host."""

    def __init__(self, min_interval: float = 2.0) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next_slot: dict[str, float] = {}

    def wait(self, key: str = "") -> float:
        """Sleep until the next slot for *key*; returns the seconds slept."""
        with self._lock:
            now = time.time()
            slot = max(now, self._next_slot.get(key, 0.0))
            self._next_slot[key] = slot + self.min_interval
        delay = slot - time.time()
        if delay > 0:
            time.sleep(delay)
            return delay
        return 0.0


_limiter_lock = threading.Lock()
_default_limiter = RateLimiter(min_interval=2.0)


def default_limiter() -> RateLimiter:
    return _default_limiter


def host_key(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return (parts.netloc or "").lower()


def _decode_body(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if "gzip" in encoding:
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    if "deflate" in encoding:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return raw
    return raw


def _decode_payload(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 200)), response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read() or b""
        except Exception:  # noqa: BLE001 - body is best effort
            pass
        return int(exc.code), body, dict(exc.headers or {})


def _load(url: str):
    try:
        return store.query_one("SELECT * FROM http_cache WHERE url=?", (url,))
    except Exception as exc:  # noqa: BLE001 - cache is optional
        LOG.debug("http cache read failed: %s", exc)
        return None


def _save(url: str, etag: str | None, body: str | None, status: int, ttl: float) -> None:
    try:
        store.execute(
            "INSERT INTO http_cache (url, etag, body, status, fetched_at, ttl_sec, next_try) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, body=excluded.body, "
            "status=excluded.status, fetched_at=excluded.fetched_at, ttl_sec=excluded.ttl_sec, "
            "next_try=NULL",
            (url, etag, body, status, time.time(), int(ttl)),
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("http cache write failed: %s", exc)


def _set_next_try(url: str, when: float) -> None:
    try:
        store.execute("UPDATE http_cache SET next_try=? WHERE url=?", (when, url))
    except Exception as exc:  # noqa: BLE001
        LOG.debug("http cache backoff write failed: %s", exc)


def _retry_after(headers: dict[str, str], default: float) -> float:
    raw = (headers or {}).get("Retry-After") or (headers or {}).get("retry-after")
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return default


def http_cached(
    url: str,
    *,
    ttl: float = DEFAULT_TTL,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    limiter: RateLimiter | None = None,
    fetcher: Fetcher | None = None,
    force: bool = False,
) -> tuple[Any, str]:
    """Fetch *url* through the cache.

    Returns `(payload, status)` where status is one of `"fresh"`, `"cache"`,
    `"stale"`, `"backoff"` or `"error"`.  `payload` is parsed JSON when possible.
    """
    cached = _load(url)
    now = time.time()
    if cached and not force and cached["body"] is not None:
        ttl_sec = float(cached["ttl_sec"] or ttl)
        if now - float(cached["fetched_at"] or 0) < max(0.0, min(ttl_sec, ttl)):
            return _decode_payload(cached["body"]), "cache"
    if cached and cached["next_try"] and float(cached["next_try"]) > now:
        if cached["body"] is not None:
            return _decode_payload(cached["body"]), "backoff"
        return None, "backoff"

    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/html;q=0.9,*/*;q=0.5",
    }
    request_headers.update(headers or {})
    if cached and cached["etag"]:
        request_headers["If-None-Match"] = cached["etag"]

    (limiter or default_limiter()).wait(host_key(url))
    fetch = fetcher or _http_get
    try:
        status, raw, response_headers = fetch(url, request_headers, timeout)
    except Exception as exc:  # noqa: BLE001 - network failure is a state, not a crash
        LOG.warning("http_cached %s failed: %s", url, exc)
        if cached and cached["body"] is not None:
            return _decode_payload(cached["body"]), "stale"
        return None, "error"

    if status == 304 and cached and cached["body"] is not None:
        _save(url, cached["etag"], cached["body"], 304, ttl)
        return _decode_payload(cached["body"]), "fresh"

    if status in (429, 503):
        delay = _retry_after(response_headers, default=120.0)
        LOG.warning("http_cached %s -> HTTP %s, backing off %.0fs", url, status, delay)
        _set_next_try(url, now + delay)
        if cached and cached["body"] is not None:
            return _decode_payload(cached["body"]), "stale"
        return None, "error"

    if status != 200:
        LOG.warning("http_cached %s -> HTTP %s", url, status)
        _set_next_try(url, now + 300.0)
        if cached and cached["body"] is not None:
            return _decode_payload(cached["body"]), "stale"
        return None, "error"

    text = _decode_body(raw, (response_headers or {}).get("Content-Encoding") or (response_headers or {}).get("content-encoding") or "").decode(
        "utf-8", "replace"
    )
    _save(url, (response_headers or {}).get("ETag") or (response_headers or {}).get("etag"), text, 200, ttl)
    return _decode_payload(text), "fresh"


def cache_clear(url: str | None = None) -> int:
    if url is None:
        return store.execute("DELETE FROM http_cache")
    return store.execute("DELETE FROM http_cache WHERE url=?", (url,))
