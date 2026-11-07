"""Provider-independent OpenAI-compatible completion (no new Agent)."""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from .core import ProductionError, unpack_model_json


def generate_json(system: str, user: str, *, api_base: str, api_key: str,
                  model: str, transport: httpx.BaseTransport | None = None) -> dict:
    if not api_base or not api_key or not model:
        raise ProductionError("Model URL, API key, and model are required")
    url = api_base.rstrip("/") + "/chat/completions"
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ProductionError("Model API base must be a valid HTTPS URL")
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

