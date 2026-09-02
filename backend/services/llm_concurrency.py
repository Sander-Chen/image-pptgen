"""In-process LLM request concurrency limiters."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Iterator
from urllib.parse import urlparse

from backend.services.system_settings import DEFAULT_PROVIDER_CONCURRENCY, get_system_settings

_lock = threading.Lock()
_provider_limiters: dict[str, tuple[int, threading.BoundedSemaphore]] = {}


def provider_key_for_config(agent_config: dict | None) -> str | None:
    if not agent_config:
        return None
    api_type = str(agent_config.get("api_type") or "").strip().lower()
    endpoint = str(agent_config.get("endpoint") or "").strip()
    if not api_type or not endpoint:
        return None
    parsed = urlparse(endpoint)
    host = parsed.hostname
    if not host and "://" not in endpoint:
        host = urlparse(f"//{endpoint}").hostname
    if not host:
        return None
    return f"{api_type}:{host.lower()}"


def provider_limit_for_key(provider_key: str | None) -> int:
    if not provider_key:
        return 10_000
    try:
        settings = get_system_settings()
        provider_settings = settings.get("provider_concurrency") or {}
    except Exception:
        provider_settings = {}
    if isinstance(provider_settings, dict) and provider_key in provider_settings:
        return max(1, int(provider_settings[provider_key]))
    if provider_key in DEFAULT_PROVIDER_CONCURRENCY:
        return DEFAULT_PROVIDER_CONCURRENCY[provider_key]
    return 10


def provider_limit_for_config(agent_config: dict | None) -> int:
    return provider_limit_for_key(provider_key_for_config(agent_config))


def _get_provider_limiter(provider_key: str) -> threading.BoundedSemaphore:
    limit = provider_limit_for_key(provider_key)
    with _lock:
        current = _provider_limiters.get(provider_key)
        if current is None or current[0] != limit:
            current = (limit, threading.BoundedSemaphore(limit))
            _provider_limiters[provider_key] = current
        return current[1]


@contextmanager
def acquire_provider_slot(agent_config: dict | None) -> Iterator[None]:
    provider_key = provider_key_for_config(agent_config)
    if not provider_key:
        yield
        return
    limiter = _get_provider_limiter(provider_key)
    limiter.acquire()
    try:
        yield
    finally:
        limiter.release()
