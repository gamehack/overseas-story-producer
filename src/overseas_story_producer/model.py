"""Provider-independent OpenAI-compatible streaming completion (no new Agent)."""
from __future__ import annotations

import codecs
import ipaddress
import json
import math
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .core import ProductionError, unpack_model_json


_MAX_RESPONSE_BYTES = 800_000


@dataclass
class _TransferDiagnostics:
    """Fixed-schema telemetry: NEVER store request/response content or exception text."""

    stage: str
    started: float = field(default_factory=time.monotonic)
    status: int | None = None
    mode: str = "unknown"
    content_type: str = "unknown"
    chunks: int = 0
    decoded_bytes: int = 0
    sse_frames: int = 0
    first_chunk_ms: int | None = None
    last_chunk_ms: int | None = None
    saw_done: bool = False
    successful: bool = False
    error_class: str = "none"

    def count_chunk(self, chunk: bytes) -> None:
        if not chunk:
            return
        now_ms = max(0, int((time.monotonic() - self.started) * 1000))
        if self.first_chunk_ms is None:
            self.first_chunk_ms = now_ms
        self.last_chunk_ms = now_ms
        self.chunks += 1
        self.decoded_bytes += len(chunk)

    def emit(self) -> None:
        # Never log model name, endpoint URL, headers, IDs, prompts, SSE data,
        # HTTPX exception messages, or response text (including on failure).
        record = {
            "event": "model_transport",
            "stage": self.stage if self.stage in {"author", "reviewer"} else "unspecified",
            "outcome": "success" if self.successful else "failed",
            "status": self.status,
            "mode": self.mode,
            "content_type": self.content_type,
            "elapsed_ms": max(0, int((time.monotonic() - self.started) * 1000)),
            "first_chunk_ms": self.first_chunk_ms,
            "last_chunk_ms": self.last_chunk_ms,
            "chunks": self.chunks,
            "decoded_bytes": self.decoded_bytes,
            "sse_frames": self.sse_frames,
            "sse_done": self.saw_done,
            "error_class": self.error_class,
        }
        try:
            print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
        except OSError:
            pass  # A closed diagnostic stream must never change production semantics.


def _safe_httpx_error_class(exc: httpx.HTTPError) -> str:
    # Only hardcoded class labels; never stringify a possibly sensitive
    # exception, request object, raw header, or redirect target.
    for label, cls in (
        ("RemoteProtocolError", httpx.RemoteProtocolError),
        ("LocalProtocolError", httpx.LocalProtocolError),
        ("ReadError", httpx.ReadError),
        ("ConnectError", httpx.ConnectError),
        ("WriteError", httpx.WriteError),
        ("CloseError", httpx.CloseError),
        ("ProxyError", httpx.ProxyError),
        ("DecodingError", httpx.DecodingError),
        ("TooManyRedirects", httpx.TooManyRedirects),
    ):
        if isinstance(exc, cls):
            return label
    return "OtherHTTPError"


def _iter_sse_lines(response: httpx.Response, diag: _TransferDiagnostics):
    """Read actual decoded-byte chunks while preserving SSE line framing.

    Handles UTF-8 and CRLF split across HTTP chunks, without retaining
    chunks in telemetry. First-chunk time measures application bytes,
    not TCP packet arrival or server-side first-token latency.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffered = ""
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        diag.count_chunk(chunk)
        buffered += decoder.decode(chunk)
        while buffered:
            cr, lf = buffered.find("\r"), buffered.find("\n")
            if cr < 0 and lf < 0:
                break
            index = min(pos for pos in (cr, lf) if pos >= 0)
            if buffered[index] == "\r" and index == len(buffered) - 1:
                break  # Could be CRLF split across chunks.
            delimiter = 2 if buffered[index:index + 2] == "\r\n" else 1
            line, buffered = buffered[:index], buffered[index + delimiter:]
            yield line
    buffered += decoder.decode(b"", final=True)
    # Stream can end with no line ending, or an isolated final CR.
    if buffered:
        for line in buffered.splitlines():
            yield line




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


def _decode_json_completion(response: httpx.Response, diag: _TransferDiagnostics) -> str:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        diag.count_chunk(chunk)
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise ProductionError("Model response exceeded size limit")
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError):
        raise ProductionError("Model response format invalid") from None
    if not isinstance(content, str):
        raise ProductionError("Model response format invalid")
    return content


def _decode_sse_completion(response: httpx.Response, diag: _TransferDiagnostics) -> str:
    """Assemble OpenAI SSE deltas, requiring an explicit final [DONE]."""
    parts: list[str] = []
    data_lines: list[str] = []
    total_chars = 0

    def consume_frame() -> None:
        nonlocal total_chars
        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data:
            return
        diag.sse_frames += 1
        if data == "[DONE]":
            diag.saw_done = True
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
                # Some compatible gateways wrap a completed message in SSE.
                message = choice.get("message")
                delta = message if isinstance(message, dict) else {}
            if not isinstance(delta, dict):
                raise ProductionError("Model stream delta format invalid")
            piece = delta.get("content")
            # Ignore reasoning_content/reasoning_text, including in telemetry.
            if piece is None:
                continue
            if not isinstance(piece, str):
                raise ProductionError("Model stream content format invalid")
            total_chars += len(piece)
            if total_chars > _MAX_RESPONSE_BYTES:
                raise ProductionError("Model stream exceeded size limit")
            parts.append(piece)

    for line in _iter_sse_lines(response, diag):
        if line == "":
            consume_frame()
            if diag.saw_done:
                break
        elif line.startswith(":"):
            continue  # SSE comment/heartbeat.
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    if not diag.saw_done:
        consume_frame()
    if not diag.saw_done:
        raise ProductionError("Model stream ended before [DONE]")
    return "".join(parts)


def generate_json(system: str, user: str, *, api_base: str, api_key: str,
                  model: str, allow_private_http: bool = False,
                  timeout_seconds: float = 120.0,
                  transport: httpx.BaseTransport | None = None,
                  transport_info: dict[str, str] | None = None,
                  stage: str = "unspecified") -> dict:
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
    diag = _TransferDiagnostics(stage=stage)
    try:
        with httpx.Client(timeout=timeout_seconds, transport=transport,
                          follow_redirects=False) as client:
            with client.stream("POST", url, json=payload, headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }) as response:
                diag.status = response.status_code
                if response.status_code != 200:
                    raise ProductionError(f"Model generation failed (HTTP {response.status_code})")
                content_type = response.headers.get("content-type", "").lower()
                diag.content_type = ("sse" if "text/event-stream" in content_type else
                                     "json" if "application/json" in content_type else
                                     "other" if content_type else "missing")
                if "text/event-stream" in content_type:
                    diag.mode = "sse"
                    content = _decode_sse_completion(response, diag)
                else:
                    diag.mode = "json_fallback"
                    # A gateway may send regular JSON despite stream=True.
                    content = _decode_json_completion(response, diag)
                if transport_info is not None:
                    transport_info["mode"] = diag.mode
        result = unpack_model_json(content)
        diag.successful = True
        return result
    except ProductionError:
        diag.error_class = "ProductionError"
        raise
    except httpx.ConnectTimeout:
        diag.error_class = "ConnectTimeout"
        raise ProductionError("Model timeout (connect)") from None
    except httpx.ReadTimeout:
        diag.error_class = "ReadTimeout"
        raise ProductionError("Model timeout (read)") from None
    except httpx.WriteTimeout:
        diag.error_class = "WriteTimeout"
        raise ProductionError("Model timeout (write)") from None
    except httpx.PoolTimeout:
        diag.error_class = "PoolTimeout"
        raise ProductionError("Model timeout (pool)") from None
    except httpx.HTTPError as exc:
        diag.error_class = _safe_httpx_error_class(exc)
        # Keep the public error generic; the diagnostic has the safe subtype.
        raise ProductionError("Model transport failure") from None
    except (KeyError, IndexError, ValueError, TypeError):
        diag.error_class = "ResponseFormatError"
        raise ProductionError("Model response format invalid") from None
    finally:
        diag.emit()
