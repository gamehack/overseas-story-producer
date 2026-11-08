import copy
import hashlib
import json
from dataclasses import replace

import pytest

from overseas_story_producer.core import (
    ProductionError, author_prompt, build_artifact, canonical_period,
    evaluate, reviewer_prompt, validate_artifact,
)


def test_reviewer_receives_same_archived_evidence_as_author(case, model_response):
    _, author_user = author_prompt(case)
    reviewer_system, reviewer_user = reviewer_prompt(case, model_response)

    author_evidence = json.loads(author_user.split("\n", 1)[1])
    reviewer_evidence = json.loads(reviewer_user)

    # The reviewer must receive every evidence source available to the author.
    for key in (
        "case_id", "original_title", "chinese_title", "source_url",
        "chinese_synopsis", "original_excerpt", "original_excerpt_truncated", "facts",
    ):
        assert reviewer_evidence[key] == author_evidence[key]

    assert reviewer_evidence["manuscript"] == {
        "title": model_response["title"],
        "body_markdown": model_response["body_markdown"],
    }
    assert "中文摘要" in reviewer_system
    assert "原始叙事正文" in reviewer_system
    assert "不得仅因某句不在结构化 facts 数组中" in reviewer_system


def test_full_source_narrative_is_included_and_editorial_advice_is_excluded(case):
    long_intro = "原始创业叙述。" * 900
    body = (
        long_intro
        + chr(10) + ':::advice{slug="editorial" category="mindset"}' + chr(10)
        + "编辑者的总结不能被当成创始人事实。"
        + chr(10) + ":::" + chr(10)
        + "迁云成本证据：Azure账单下降到每月1500美元。"
    )
    full_case = replace(case, body_excerpt=body)
    system, prompt = author_prompt(full_case)
    evidence = json.loads(prompt.split("\n", 1)[1])

    assert len(evidence["original_excerpt"]) > 5000
    assert "迁云成本证据" in evidence["original_excerpt"]
    assert "编辑者的总结不能被当成创始人事实" not in evidence["original_excerpt"]
    assert evidence["original_excerpt_truncated"] is False
    assert "不得从收入、客户数、月增长率推断网站访问量" in system
    assert "据Mihai自述" in system


def test_source_evidence_truncation_is_explicit(case):
    full_case = replace(case, body_excerpt="原始证据。" * 5000)
    _, prompt = author_prompt(full_case)
    evidence = json.loads(prompt.split("\n", 1)[1])
    assert len(evidence["original_excerpt"]) == 20000
    assert evidence["original_excerpt_truncated"] is True


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

