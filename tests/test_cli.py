"""The two model passes are serialized without automatic retries."""
from __future__ import annotations

import pytest

from overseas_story_producer import cli
from overseas_story_producer.core import ProductionError


@pytest.fixture
def setup_flow(monkeypatch, case, model_response, model_review):
    events = []
    monkeypatch.setenv("DATABASE_URL", "not-used-in-test")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "fake-model")
    monkeypatch.delenv("OPENAI_COMPAT_REVIEW_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(cli, "load_case", lambda *_: case)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: events.append(("sleep", seconds)))

    def fake_completion(system, user, **kwargs):
        phase = "author" if not any(item[0] == "author" for item in events) else "review"
        events.append((phase, kwargs["model"]))
        return model_response if phase == "author" else model_review

    monkeypatch.setattr(cli, "generate_json", fake_completion)
    return events


def _run(tmp_path):
    output = tmp_path / "story.json"
    result = cli.run([
        "generate", "--story-id", "11111111-1111-4111-8111-111111111111",
        "--issue-number", "01", "--output", str(output),
    ])
    return result, output


def test_review_waits_after_successful_author(tmp_path, setup_flow):
    result, output = _run(tmp_path)
    assert result["status"] == "LOCAL_PENDING_REVIEW"
    assert result["qa"]["passes_automatic_checks"] is True
    assert output.is_file()
    assert setup_flow == [
        ("author", "fake-model"),
        ("sleep", 90.0),
        ("review", "fake-model"),
    ]


def test_zero_interval_skips_sleep(tmp_path, setup_flow, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_REVIEW_COOLDOWN_SECONDS", "0")
    _run(tmp_path)
    assert [event[0] for event in setup_flow] == ["author", "review"]


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "601", "abc", ""])
def test_invalid_cooldown_fails_before_model(tmp_path, setup_flow, monkeypatch, value):
    monkeypatch.setenv("OPENAI_COMPAT_REVIEW_COOLDOWN_SECONDS", value)
    with pytest.raises(ProductionError, match="cooldown"):
        _run(tmp_path)
    assert setup_flow == []


def test_review_rate_limit_is_terminal(tmp_path, setup_flow, monkeypatch):
    def fail_second(system, user, **kwargs):
        phase = "author" if not any(x[0] == "author" for x in setup_flow) else "review"
        setup_flow.append((phase, "fake-model"))
        if phase == "review":
            raise ProductionError("Model generation failed (HTTP 429)")
        return {"title": "temporary draft"}

    monkeypatch.setattr(cli, "generate_json", fail_second)
    with pytest.raises(ProductionError, match="HTTP 429"):
        _run(tmp_path)
    assert [event[0] for event in setup_flow] == ["author", "sleep", "review"]
    assert not (tmp_path / "story.json").exists()


def test_failed_author_does_not_sleep_or_review(tmp_path, setup_flow, monkeypatch):
    def fail_first(system, user, **kwargs):
        setup_flow.append(("author", "fake-model"))
        raise ProductionError("Model generation failed (HTTP 429)")

    monkeypatch.setattr(cli, "generate_json", fail_first)
    with pytest.raises(ProductionError, match="HTTP 429"):
        _run(tmp_path)
    assert [event[0] for event in setup_flow] == ["author"]


@pytest.mark.parametrize("value", ["nan", "inf", "9", "301", "abc", ""])
def test_invalid_model_timeout_fails_before_model(tmp_path, setup_flow, monkeypatch, value):
    monkeypatch.setenv("OPENAI_COMPAT_TIMEOUT_SECONDS", value)
    with pytest.raises(ProductionError, match="timeout"):
        _run(tmp_path)
    assert setup_flow == []


def test_configured_model_timeout_passes_to_both_stages(tmp_path, setup_flow, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_TIMEOUT_SECONDS", "150")
    seen = []

    def fake_completion(system, user, **kwargs):
        seen.append(kwargs["timeout_seconds"])
        phase = "author" if len(seen) == 1 else "review"
        setup_flow.append((phase, kwargs["model"]))
        if phase == "author":
            return {"title": "sample"}
        return {"dimensions": {}}

    monkeypatch.setattr(cli, "generate_json", fake_completion)
    _run(tmp_path)
    assert seen == [150.0, 150.0]


@pytest.mark.parametrize(("fail_stage", "label"), [
    ("author", "Author stage"),
    ("review", "Reviewer stage"),
])
def test_stage_specific_timeout_does_not_retry(tmp_path, setup_flow, monkeypatch, fail_stage, label):
    model_response = {
        "title": "temporary draft", "body_markdown": "short",
        "facts_used": [], "uncertainties": [],
    }

    def fake_completion(system, user, **kwargs):
        phase = "author" if not any(event[0] == "author" for event in setup_flow) else "review"
        setup_flow.append((phase, kwargs["model"]))
        if phase == fail_stage:
            raise ProductionError("Model timeout (read)")
        return model_response

    monkeypatch.setattr(cli, "generate_json", fake_completion)
    with pytest.raises(ProductionError, match=label + ": Model timeout"):
        _run(tmp_path)
    phases = [event[0] for event in setup_flow]
    assert phases == (["author"] if fail_stage == "author" else ["author", "sleep", "review"])
    assert not (tmp_path / "story.json").exists()
