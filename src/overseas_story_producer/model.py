"""Provider-independent OpenAI-compatible completion (no new Agent)."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx

from .core import ProductionError, unpack_model_json


def _private_model_host(host: str) -> bool:
    # Only direct private IPs and known local Docker/host aliases may use HTTP.
    if host.lower() in {"localhost", "omniroute", "host.docker.internal"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_unspecified and (address.is_private or address.is_loopback)


def _completion_url(api_base: str, *, allow_private_http: bool) -> str:
    base = api_base.rstrip("/")
    try:
        parsed = urlparse(base)
        host = parsed.hostname
    except ValueError:
        raise ProductionError("Invalid model API URL") from None
    if (not parsed.netloc or not host or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.params):
        raise ProductionError("Invalid model API URL")
    if parsed.scheme != "https":
        if not (parsed.scheme == "http" and allow_private_http and _private_model_host(host)):
            raise ProductionError("Model API base must use HTTPS, or opt-in private HTTP")
    if parsed.path.rstrip("/").endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def generate_json(system: str, user: str, *, api_base: str, api_key: str,
                  model: str, allow_private_http: bool = False,
                  transport: httpx.BaseTransport | None = None) -> dict:
    if not api_base or not api_key or not model:
        raise ProductionError("Model URL, API key, and model are required")
    url = _completion_url(api_base, allow_private_http=allow_private_http)
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    try:
        with httpx.Client(timeout=120, transport=transport, follow_redirects=False) as client:
            response = client.post(url, json=payload, headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            })
        if response.status_code != 200 or len(response.content) > 800_000:
            raise ProductionError(f"Model generation failed (HTTP {response.status_code})")
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Unexpected content")
    except ProductionError:
        raise
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
        raise ProductionError("Model connection or response failed") from None
    return unpack_model_json(content)

