import copy
import hashlib

import pytest

from overseas_story_producer.core import (
    ProductionError, author_prompt, build_artifact, canonical_period,
    evaluate, validate_artifact,
)


def test_valid_story_and_artifact(case, model_response, model_review):
    system, user = author_prompt(case)
    assert "不能" in user and "JSON" in system
    artifact = build_artifact(case, "2026-11-09", model_response, model_review)
    assert artifact["qa"]["passes_automatic_checks"]
    assert artifact["source_key"] == "overseas-stories:2026-11-09"
    assert artifact["status"] == "PENDING_HUMAN_REVIEW"
    assert artifact["qa"]["editorial_score"] == 94
    validate_artifact(artifact)


def test_fail_closed_on_unknown_facts(case, model_response):
    model_response["facts_used"] = ["unknown"] + model_response["facts_used"]
    assert not evaluate(case, "2026-11-09", model_response)["passes_automatic_checks"]


def test_fail_closed_on_ready_injection(case, model_response, model_review):
    model_response["body_markdown"] += "\nQJAI_PUBLISH_READY test"
    artifact = build_artifact(case, "2026-11-09", model_response, model_review)
    with pytest.raises(ProductionError, match="READY"):
        validate_artifact(artifact)


def test_tampered_artifact(case, model_response, model_review):
    artifact = build_artifact(case, "2026-11-09", model_response, model_review)
    artifact["body_markdown"] += "改变后的正文"
    with pytest.raises(ProductionError, match="modified"):
        validate_artifact(artifact)


def test_cannot_approve_automatically(case, model_response, model_review):
    artifact = build_artifact(case, "2026-11-09", model_response, model_review)
    artifact["publish_ready"] = True
    with pytest.raises(ProductionError, match="pending"):
        validate_artifact(artifact)


def test_missing_editorial_review_cannot_submit(case, model_response):
    item = build_artifact(case, "2026-11-09", model_response)
    with pytest.raises(ProductionError, match="automatic"):
        validate_artifact(item)


def test_unverified_claims_fail_editorial_gate(case, model_response, model_review):
    model_review["unsupported_claims"] = ["未注明出处的收入数字"]
    item = build_artifact(case, "2026-11-09", model_response, model_review)
    assert not item["qa"]["passes_automatic_checks"]


def test_bad_editorial_schema_fails(case, model_response, model_review):
    model_review["dimensions"]["conflict"] = True
    item = build_artifact(case, "2026-11-09", model_response, model_review)
    assert not item["qa"]["passes_automatic_checks"]


@pytest.mark.parametrize("period", ["2026-2-9", "garbage", "2026-02-30"])
def test_reject_bad_period(period):
    with pytest.raises(ProductionError):
        canonical_period(period)

