import httpx
import pytest

from overseas_story_producer.core import ProductionError
from overseas_story_producer.model import generate_json


def _completion(api_base: str, *, allow_private_http: bool = False) -> str:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer internal-test-key"
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
