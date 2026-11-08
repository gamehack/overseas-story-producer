"""Non-billable public HTTPS probe for the configured OmniRoute API endpoint.

Unauthenticated GET is expected to return 401 from the gateway's auth layer.
This proves routing and TLS, not successful authenticated model inference.
"""
from __future__ import annotations

import sys
from urllib.parse import urlsplit

import httpx


PUBLIC_ENDPOINT = "https://omniroute.qjai.org/v1/chat/completions"


class ProbeError(RuntimeError):
    pass


def probe_gateway(
    url: str = PUBLIC_ENDPOINT, *,
    transport: httpx.BaseTransport | None = None,
) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https"
            or parsed.netloc != "omniroute.qjai.org"
            or parsed.path != "/v1/chat/completions"
            or parsed.query or parsed.fragment):
        raise ProbeError("Unexpected gateway probe target")
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        raise ProbeError("Public gateway HTTPS probe failed") from None
    # Do not inspect or print a response body, which could contain sensitive data.
    if response.status_code != 401:
        raise ProbeError(f"Unexpected public gateway status: HTTP {response.status_code}")


def main() -> int:
    try:
        probe_gateway()
    except ProbeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Public OmniRoute HTTPS/TLS/auth-boundary probe passed (HTTP 401)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
