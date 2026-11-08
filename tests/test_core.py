import copy
import hashlib
import json

import pytest

from overseas_story_producer.core import (
    ProductionError, author_prompt, build_artifact, canonical_period,
    evaluate, reviewer_prompt, validate_artifact,
)


def test_reviewer_receives_same_archived_evidence_as_author(case, model_response):
    _, author_user = author_prompt(case)
    reviewer_system, reviewer_user = reviewer_prompt(case, model_response)

    author_evidence = json.loads(author_user.split("\\n", 1)[1])
    reviewer_evidence = json.loads(reviewer_user)

    # The reviewer must receive every evidence source available to the author.
    for key in (
        "case_id", "original_title", "chinese_title", "source_url",
        "chinese_synopsis", "original_excerpt", "facts",
    ):
        assert reviewer_evidence[key] == author_evidence[key]

    assert reviewer_evidence["manuscript"] == {
        "title": model_response["title"],
        "body_markdown": model_response["body_markdown"],
    }
    assert "中文摘要" in reviewer_system
    assert "原文摘录" in reviewer_system
    assert "不得仅因某句未出现在结构化 facts 数组中" in reviewer_system


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

