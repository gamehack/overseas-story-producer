"""The automated gateway check never sends an API key or a model request."""
import httpx
import pytest

from overseas_story_producer.gateway_probe import ProbeError, probe_gateway


def _mock(status, seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(status)
    return httpx.MockTransport(handler)


def test_gateway_probe_expects_unauthenticated_401():
    seen = []
    probe_gateway(transport=_mock(401, seen))
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "GET"
    assert req.url.scheme == "https"
    assert req.url.host == "omniroute.qjai.org"
    assert req.url.path == "/v1/chat/completions"
    assert "authorization" not in req.headers


@pytest.mark.parametrize("status", [200, 301, 302, 400, 403, 404, 405, 429, 500])
def test_unexpected_gateway_status_fails(status):
    seen = []
    with pytest.raises(ProbeError, match=f"HTTP {status}"):
        probe_gateway(transport=_mock(status, seen))
    assert len(seen) == 1


@pytest.mark.parametrize("url", [
    "http://omniroute.qjai.org/v1/chat/completions",
    "https://evil.example/v1/chat/completions",
    "https://omniroute.qjai.org:8443/v1/chat/completions",
    "https://omniroute.qjai.org/v1/models",
    "https://omniroute.qjai.org/v1/chat/completions?secret=1",
])
def test_probe_rejects_unapproved_targets_before_request(url):
    seen = []
    with pytest.raises(ProbeError, match="target"):
        probe_gateway(url, transport=_mock(401, seen))
    assert not seen


def test_tls_network_failure_is_safely_reported():
    def handler(request):
        raise httpx.ConnectError("private host diagnostic", request=request)
    with pytest.raises(ProbeError, match="HTTPS probe failed") as exc:
        probe_gateway(transport=httpx.MockTransport(handler))
    assert "private host diagnostic" not in str(exc.value)
