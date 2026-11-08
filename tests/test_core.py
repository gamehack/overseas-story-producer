import copy
import hashlib
import json
from dataclasses import replace

import pytest

from overseas_story_producer.core import (
    ProductionError, author_prompt, build_artifact, canonical_issue_number,
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


def test_artifact_deterministically_adds_missing_source_note(case, model_response, model_review):
    response = dict(model_response)
    response["body_markdown"] = response["body_markdown"].replace(case.source_url, "")
    artifact = build_artifact(case, "01", response, model_review)

    assert case.source_url in artifact["body_markdown"]
    assert "## 资料来源" in artifact["body_markdown"]
    assert "Original source URL must appear in source note" not in artifact["qa"]["issues"]
    assert artifact["qa"]["score"] == 100
    assert artifact["qa"]["passes_automatic_checks"] is True


def test_author_and_reviewer_prompts_forbid_customer_type_and_causal_inference(case, model_response):
    author_system, _ = author_prompt(case)
    reviewer_system, _ = reviewer_prompt(case, model_response)

    assert "个人订阅" in author_system
    assert "因果关系" in author_system
    assert "个人订阅" in reviewer_system
    assert "时间或因果关系" in reviewer_system
    assert "中英混杂" in author_system
    assert "不承担独立事实核验" in author_system
    assert "不是独立事实核验" in reviewer_system
    assert "仅用于真正严重的编造" in reviewer_system


def test_valid_story_and_artifact(case, model_response, model_review):
    system, user = author_prompt(case)
    assert "不能" in user and "JSON" in system
    artifact = build_artifact(case, "01", model_response, model_review)
    assert artifact["qa"]["passes_automatic_checks"]
    assert artifact["source_key"] == "overseas-stories:01"
    assert artifact["issue_number"] == "01"
    assert artifact["notion_title"].startswith("01｜海外创业志｜")
    assert artifact["status"] == "PENDING_HUMAN_REVIEW"
    assert artifact["qa"]["editorial_score"] == 94
    validate_artifact(artifact)


def test_fail_closed_on_unknown_facts(case, model_response):
    model_response["facts_used"] = ["unknown"] + model_response["facts_used"]
    assert not evaluate(case, "01", model_response)["passes_automatic_checks"]


def test_fail_closed_on_ready_injection(case, model_response, model_review):
    model_response["body_markdown"] += "\nQJAI_PUBLISH_READY test"
    artifact = build_artifact(case, "01", model_response, model_review)
    with pytest.raises(ProductionError, match="READY"):
        validate_artifact(artifact)


def test_tampered_artifact(case, model_response, model_review):
    artifact = build_artifact(case, "01", model_response, model_review)
    artifact["body_markdown"] += "改变后的正文"
    with pytest.raises(ProductionError, match="modified"):
        validate_artifact(artifact)


def test_cannot_approve_automatically(case, model_response, model_review):
    artifact = build_artifact(case, "01", model_response, model_review)
    artifact["publish_ready"] = True
    with pytest.raises(ProductionError, match="pending"):
        validate_artifact(artifact)


def test_missing_editorial_review_cannot_submit(case, model_response):
    item = build_artifact(case, "01", model_response)
    with pytest.raises(ProductionError, match="automatic"):
        validate_artifact(item)


def test_minor_source_deviations_are_warnings_and_allow_human_review(case, model_response, model_review):
    model_review["unsupported_claims"] = ["太阳能板安装在屋顶", "六层菜单"]
    model_review["issues"] = ["请把六层菜单改为六个不同菜单"]
    item = build_artifact(case, "01", model_response, model_review)
    assert item["qa"]["passes_automatic_checks"]
    assert item["qa"]["editorial_warnings"] == ["太阳能板安装在屋顶", "六层菜单"]
    assert item["status"] == "PENDING_HUMAN_REVIEW"
    assert item["publish_ready"] is False
    validate_artifact(item)


def test_major_fabrication_is_still_blocked(case, model_response, model_review):
    model_review["unsupported_claims"] = ["虚构的离婚经历"]
    model_review["hard_fail"] = True
    model_review["hard_fail_reason"] = "虚构婚姻破裂这一重大人生事件"
    item = build_artifact(case, "01", model_response, model_review)
    assert not item["qa"]["passes_automatic_checks"]
    assert any("Hard failure" in issue for issue in item["qa"]["editorial_issues"])
    with pytest.raises(ProductionError, match="automatic"):
        validate_artifact(item)


def test_major_fabrication_requires_explicit_reason(case, model_response, model_review):
    model_review["hard_fail"] = True
    model_review["hard_fail_reason"] = " "
    item = build_artifact(case, "01", model_response, model_review)
    assert not item["qa"]["passes_automatic_checks"]


def test_warning_integrity_is_checked_before_submission(case, model_response, model_review):
    model_review["unsupported_claims"] = ["屋顶安装位置无证据"]
    item = build_artifact(case, "01", model_response, model_review)
    item["qa"]["editorial_warnings"] = []
    with pytest.raises(ProductionError, match="editorial review"):
        validate_artifact(item)


def test_bad_editorial_schema_fails(case, model_response, model_review):
    model_review["dimensions"]["conflict"] = True
    item = build_artifact(case, "01", model_response, model_review)
    assert not item["qa"]["passes_automatic_checks"]


@pytest.mark.parametrize("issue_number", ["00", "garbage", "2026-11-09", "001", "1", "100000"])
def test_reject_bad_issue_number(issue_number):
    with pytest.raises(ProductionError):
        canonical_issue_number(issue_number)


@pytest.mark.parametrize("issue_number", ["01", "09", "10", "99999"])
def test_accept_canonical_issue_number(issue_number):
    assert canonical_issue_number(issue_number) == issue_number

