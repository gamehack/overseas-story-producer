import httpx
import pytest

from overseas_story_producer.core import ProductionError
from overseas_story_producer.model import generate_json


def _completion(api_base: str, *, allow_private_http: bool = False) -> str:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer internal-test-key"
        assert request.headers["Accept"] == "text/event-stream"
        assert __import__("json").loads(request.content)["stream"] is True
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )

    result = generate_json(
        "system", "user",
        api_base=api_base, api_key="internal-test-key", model="test-model",
        allow_private_http=allow_private_http,
        transport=httpx.MockTransport(handler),
    )
    assert result == {"ok": True}
    assert len(seen) == 1
    return seen[0]


def test_https_base_appends_completion_path():
    assert _completion("https://model.example.com/v1") == (
        "https://model.example.com/v1/chat/completions"
    )


def test_https_full_endpoint_does_not_duplicate_path():
    assert _completion("https://model.example.com/v1/chat/completions") == (
        "https://model.example.com/v1/chat/completions"
    )


def test_private_omniroute_http_requires_explicit_opt_in():
    with pytest.raises(ProductionError, match="HTTPS"):
        _completion("http://omniroute:3000/v1/chat/completions")
    assert _completion(
        "http://omniroute:3000/v1/chat/completions",
        allow_private_http=True,
    ) == "http://omniroute:3000/v1/chat/completions"


@pytest.mark.parametrize("base", [
    "http://10.0.0.4:3000/v1",
    "http://172.18.0.9:3000/v1/chat/completions",
    "http://127.0.0.1:3000/v1",
])
def test_private_ip_http_with_opt_in(base):
    assert _completion(base, allow_private_http=True).endswith(
        "/v1/chat/completions"
    )


@pytest.mark.parametrize("base", [
    "http://public.example.com/v1",
    "http://8.8.8.8/v1",
    "http://omniroute.evil.example/v1",
])
def test_public_http_refused_even_with_opt_in(base):
    with pytest.raises(ProductionError, match="HTTPS"):
        _completion(base, allow_private_http=True)


@pytest.mark.parametrize("base", [
    "https://user:password@model.example.com/v1",
    "https://model.example.com/v1?secret=abc",
    "https://model.example.com/v1#fragment",
])
def test_credentials_and_query_in_url_refused(base):
    with pytest.raises(ProductionError, match="Invalid model API URL"):
        _completion(base, allow_private_http=True)


@pytest.mark.parametrize(("exc_type", "label"), [
    (httpx.ConnectTimeout, "connect"),
    (httpx.ReadTimeout, "read"),
    (httpx.WriteTimeout, "write"),
    (httpx.PoolTimeout, "pool"),
])
def test_timeout_subtype_reported_without_sensitive_details(exc_type, label):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("secret diagnostics should not leak", request=request)

    with pytest.raises(ProductionError, match=f"^Model timeout [(]{label}[)]$") as error:
        generate_json(
            "system", "user",
            api_base="https://model.example.com/v1", api_key="internal-test-key",
            model="test-model", transport=httpx.MockTransport(handler),
        )
    assert "internal-test-key" not in str(error.value)
    assert "secret diagnostics" not in str(error.value)


def test_transport_error_does_not_expose_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("token=internal-test-key", request=request)

    with pytest.raises(ProductionError, match="^Model transport failure$"):
        generate_json(
            "system", "user",
            api_base="https://model.example.com/v1", api_key="internal-test-key",
            model="test-model", transport=httpx.MockTransport(handler),
        )


def test_custom_timeout_is_applied_without_network():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"] == {
            "connect": 150.0, "read": 150.0, "write": 150.0, "pool": 150.0,
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    assert generate_json(
        "system", "user",
        api_base="https://model.example.com/v1", api_key="internal-test-key",
        model="test-model", timeout_seconds=150.0,
        transport=httpx.MockTransport(handler),
    ) == {"ok": True}


@pytest.mark.parametrize("invalid", [0.0, 9.0, 301.0, float("nan"), float("inf")])
def test_invalid_timeout_rejected_before_request(invalid):
    with pytest.raises(ProductionError, match="Model timeout"):
        generate_json(
            "system", "user",
            api_base="https://model.example.com/v1", api_key="internal-test-key",
            model="test-model", timeout_seconds=invalid,
        )



def test_sse_stream_is_assembled_and_reasoning_is_not_included():
    body = (
        b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"hidden reasoning"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"{\\\"ok\\\":"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" true}"}}]}\n\n'
        b'data: [DONE]\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body,
        )

    transport_info = {}
    assert generate_json(
        "system", "user",
        api_base="https://model.example.com/v1", api_key="internal-test-key",
        model="test-model", transport=httpx.MockTransport(handler),
        transport_info=transport_info,
    ) == {"ok": True}
    assert transport_info == {"mode": "sse"}


def test_json_fallback_transport_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    transport_info = {}
    assert generate_json(
        "system", "user",
        api_base="https://model.example.com/v1", api_key="internal-test-key",
        model="test-model", transport=httpx.MockTransport(handler),
        transport_info=transport_info,
    ) == {"ok": True}
    assert transport_info == {"mode": "json_fallback"}


def test_sse_stream_without_done_is_rejected_as_incomplete():
    body = b'data: {"choices":[{"delta":{"content":"{\\\"ok\\\": true}"}}]}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body,
        )

    with pytest.raises(ProductionError, match="ended before \\[DONE\\]"):
        generate_json(
            "system", "user",
            api_base="https://model.example.com/v1", api_key="internal-test-key",
            model="test-model", transport=httpx.MockTransport(handler),
        )


def test_malformed_sse_event_is_rejected_without_exposing_payload():
    body = b'data: token=internal-test-key\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body,
        )

    with pytest.raises(ProductionError, match="invalid SSE JSON") as error:
        generate_json(
            "system", "user",
            api_base="https://model.example.com/v1", api_key="internal-test-key",
            model="test-model", transport=httpx.MockTransport(handler),
        )
    assert "internal-test-key" not in str(error.value)
