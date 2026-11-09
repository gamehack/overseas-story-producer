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


class _ChunkedBody(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes], *, fail: Exception | None = None):
        self.chunks = chunks
        self.fail = fail

    def __iter__(self):
        yield from self.chunks
        if self.fail is not None:
            raise self.fail

    def close(self):
        pass


def _last_safe_diagnostic(capsys) -> dict:
    import json
    data = capsys.readouterr().err.strip().splitlines()
    assert len(data) == 1
    return json.loads(data[0])


def test_sse_diagnostics_measure_chunks_and_split_crlf_without_logging_content(capsys):
    import json
    secret = "private-model-message-xyz"
    event = {"choices": [{"delta": {"content": '{"ok": true}'}}]}
    body = (b"data: " + json.dumps(event).encode("utf-8") +
            b"\r\n\r\n: heartbeat\r\n\r\ndata: [DONE]\r\n\r\n")
    chunks = [body[:9], body[9:15], body[15:31], body[31:43], body[43:]]
    def handler(request):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              stream=_ChunkedBody(chunks))

    result = generate_json(secret, secret, api_base="https://model.example.com/v1",
                           api_key="secret-key-xyz", model="test-model",
                           transport=httpx.MockTransport(handler), stage="reviewer")
    assert result == {"ok": True}
    info = _last_safe_diagnostic(capsys)
    assert info["event"] == "model_transport"
    assert info["stage"] == "reviewer"
    assert info["status"] == 200
    assert info["mode"] == "sse"
    assert info["content_type"] == "sse"
    assert info["outcome"] == "success"
    assert info["sse_done"] is True
    assert info["sse_frames"] == 2
    assert info["chunks"] == len(chunks)
    assert info["decoded_bytes"] == len(body)
    assert 0 <= info["first_chunk_ms"] <= info["last_chunk_ms"] <= info["elapsed_ms"]
    assert "secret" not in json.dumps(info)
    assert "private-model-message-xyz" not in json.dumps(info)


def test_broken_sse_diagnostics_records_fixed_remote_protocol_type_only(capsys):
    import json
    secret = "PRIVATE_TOKEN_WITH_CONTENT_999"
    body = b'data: {"choices":[{"delta":{"content":"{\\"ok\\":"}}]}\n\n'
    def handler(request):
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            stream=_ChunkedBody(
                [body],
                fail=httpx.RemoteProtocolError("secret exception " + secret,
                                                request=request),
            ),
        )
    with pytest.raises(ProductionError, match=r"^Model transport failure$"):
        generate_json(secret, secret, api_base="https://model.example.com/v1",
                      api_key=secret, model="test-model",
                      transport=httpx.MockTransport(handler), stage="reviewer")
    info = _last_safe_diagnostic(capsys)
    assert info["outcome"] == "failed"
    assert info["error_class"] == "RemoteProtocolError"
    assert info["mode"] == "sse"
    assert info["status"] == 200
    assert info["chunks"] == 1
    assert info["decoded_bytes"] == len(body)
    assert info["sse_done"] is False
    assert secret not in json.dumps(info)


def test_connection_failure_diagnostic_has_no_url_or_secret(capsys):
    import json
    secret = "sensitive-header-credential-xyz"
    def handler(request):
        raise httpx.ConnectError("Authorization: " + secret, request=request)
    with pytest.raises(ProductionError, match="^Model transport failure$"):
        generate_json("system private", "user private",
                      api_base="https://model.example.com/v1",
                      api_key=secret, model="test-model",
                      transport=httpx.MockTransport(handler), stage="author")
    info = _last_safe_diagnostic(capsys)
    assert info["error_class"] == "ConnectError"
    assert info["stage"] == "author"
    assert info["status"] is None
    assert info["chunks"] == 0
    assert info["first_chunk_ms"] is None
    assert info["outcome"] == "failed"
    assert secret not in json.dumps(info)
    assert "model.example.com" not in json.dumps(info)
    assert "user private" not in json.dumps(info)


def test_sse_end_without_done_reports_incomplete_without_leaking_data(capsys):
    def handler(request):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              stream=_ChunkedBody([b'data: {"choices":[]}\n\n']))
    with pytest.raises(ProductionError, match=r"ended before \[DONE\]"):
        generate_json("system", "user", api_base="https://model.example.com/v1",
                      api_key="secret-key", model="test-model", stage="reviewer",
                      transport=httpx.MockTransport(handler))
    info = _last_safe_diagnostic(capsys)
    assert info["outcome"] == "failed"
    assert info["error_class"] == "ProductionError"
    assert info["sse_frames"] == 1
    assert info["sse_done"] is False


def test_http_status_diagnostic_does_not_read_upstream_error_body(capsys):
    import json
    private = "do-not-log-this-json"
    def handler(request):
        return httpx.Response(429, json={"error": private})
    with pytest.raises(ProductionError, match=r"HTTP 429"):
        generate_json("system", "user", api_base="https://model.example.com/v1",
                      api_key="secret-key", model="test-model", stage="author",
                      transport=httpx.MockTransport(handler))
    info = _last_safe_diagnostic(capsys)
    assert info["status"] == 429
    assert info["chunks"] == 0
    assert info["error_class"] == "ProductionError"
    assert private not in json.dumps(info)


def test_json_fallback_diagnostic_tracks_bytes_without_exposing_json(capsys):
    import json
    def handler(request):
        return httpx.Response(200, json={"choices": [
            {"message": {"content": '{"ok": true}'}}]})
    result = generate_json("system", "user", api_base="https://model.example.com/v1",
                           api_key="secret-key", model="test-model",
                           transport=httpx.MockTransport(handler), stage="author")
    assert result == {"ok": True}
    info = _last_safe_diagnostic(capsys)
    assert info["status"] == 200
    assert info["mode"] == "json_fallback"
    assert info["content_type"] == "json"
    assert info["chunks"] > 0
    assert info["decoded_bytes"] > 0
    assert info["sse_done"] is False
    assert info["outcome"] == "success"
