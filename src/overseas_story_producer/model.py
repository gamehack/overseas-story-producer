"""Provider-independent OpenAI-compatible streaming completion (no new Agent)."""
from __future__ import annotations

import ipaddress
import json
import math
from urllib.parse import urlparse

import httpx

from .core import ProductionError, unpack_model_json


_MAX_RESPONSE_BYTES = 800_000


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


def _decode_json_completion(response: httpx.Response) -> str:
    raw = response.read()
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ProductionError("Model response exceeded size limit")
    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError):
        raise ProductionError("Model response format invalid") from None
    if not isinstance(content, str):
        raise ProductionError("Model response format invalid")
    return content


def _decode_sse_completion(response: httpx.Response) -> str:
    """Assemble OpenAI Chat Completions SSE deltas; reject incomplete streams."""
    parts: list[str] = []
    data_lines: list[str] = []
    total_chars = 0
    saw_done = False

    def consume_frame() -> None:
        nonlocal total_chars, saw_done
        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data:
            return
        if data == "[DONE]":
            saw_done = True
            return
        try:
            event = json.loads(data)
        except ValueError:
            raise ProductionError("Model stream contained invalid SSE JSON") from None
        if not isinstance(event, dict):
            raise ProductionError("Model stream event format invalid")
        if event.get("error") is not None:
            raise ProductionError("Model stream returned an upstream error")
        choices = event.get("choices", [])
        if not isinstance(choices, list):
            raise ProductionError("Model stream choices format invalid")
        for choice in choices:
            if not isinstance(choice, dict):
                raise ProductionError("Model stream choice format invalid")
            delta = choice.get("delta")
            if delta is None:
                # Some compatible gateways may wrap a completed message in an SSE frame.
                message = choice.get("message")
                delta = message if isinstance(message, dict) else {}
            if not isinstance(delta, dict):
                raise ProductionError("Model stream delta format invalid")
            piece = delta.get("content")
            # Deliberately ignore reasoning_content/reasoning_text; only final
            # assistant content is part of the JSON artifact.
            if piece is None:
                continue
            if not isinstance(piece, str):
                raise ProductionError("Model stream content format invalid")
            total_chars += len(piece)
            if total_chars > _MAX_RESPONSE_BYTES:
                raise ProductionError("Model stream exceeded size limit")
            parts.append(piece)

    for line in response.iter_lines():
        if line == "":
            consume_frame()
            if saw_done:
                break
            continue
        if line.startswith(":"):
            # SSE comment/heartbeat.
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    if not saw_done:
        consume_frame()
    if not saw_done:
        raise ProductionError("Model stream ended before [DONE]")
    return "".join(parts)


def generate_json(system: str, user: str, *, api_base: str, api_key: str,
                  model: str, allow_private_http: bool = False,
                  timeout_seconds: float = 120.0,
                  transport: httpx.BaseTransport | None = None) -> dict:
    if not api_base or not api_key or not model:
        raise ProductionError("Model URL, API key, and model are required")
    if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 10 <= timeout_seconds <= 300:
        raise ProductionError("Model timeout must be between 10 and 300 seconds")
    url = _completion_url(api_base, allow_private_http=allow_private_http)
    payload = {
        "model": model,
        "temperature": 0.3,
        "stream": True,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    try:
        with httpx.Client(timeout=timeout_seconds, transport=transport,
                          follow_redirects=False) as client:
            with client.stream("POST", url, json=payload, headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }) as response:
                if response.status_code != 200:
                    raise ProductionError(f"Model generation failed (HTTP {response.status_code})")
                content_type = response.headers.get("content-type", "").lower()
                if "text/event-stream" in content_type:
                    content = _decode_sse_completion(response)
                else:
                    # Preserve compatibility with gateways/providers that return a
                    # normal JSON completion despite the stream preference.
                    content = _decode_json_completion(response)
    except ProductionError:
        raise
    except httpx.ConnectTimeout:
        raise ProductionError("Model timeout (connect)") from None
    except httpx.ReadTimeout:
        raise ProductionError("Model timeout (read)") from None
    except httpx.WriteTimeout:
        raise ProductionError("Model timeout (write)") from None
    except httpx.PoolTimeout:
        raise ProductionError("Model timeout (pool)") from None
    except httpx.HTTPError:
        raise ProductionError("Model transport failure") from None
    except (KeyError, IndexError, ValueError, TypeError):
        raise ProductionError("Model response format invalid") from None
    return unpack_model_json(content)
