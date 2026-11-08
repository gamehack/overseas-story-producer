import httpx
import pytest

from overseas_story_producer.core import ProductionError, build_artifact
from overseas_story_producer.notion import NotionDraftWriter, _to_blocks


PARENT_ID = "22222222-2222-4222-8222-222222222222"


def _response(request, *, title=None):
    if request.url.path.endswith("/children"):
        rows = [] if title is None else [{"type": "child_page", "child_page": {"title": title}}]
        return httpx.Response(200, json={"results": rows, "has_more": False})
    if request.url.path == "/v1/pages":
        return httpx.Response(200, json={
            "id": "11111111-1111-4111-8111-111111111111",
            "parent": {"type": "page_id", "page_id": PARENT_ID},
            "url": "https://www.notion.so/test",
        })
    return httpx.Response(404)


def test_dry_run_creates_nothing(case, model_response, model_review):
    artifact = build_artifact(case, "01", model_response, model_review)
    verbs = []

    def handler(request):
        verbs.append(request.method)
        return _response(request)

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        result = writer.create_pending_page(artifact)
    assert result["status"] == "DRY_RUN"
    assert verbs == ["GET"]


def test_commit_create_pending_not_ready(case, model_response, model_review):
    model_review["unsupported_claims"] = ["太阳能板屋顶位置无来源支持"]
    artifact = build_artifact(case, "01", model_response, model_review)
    sent = []

    def handler(request):
        if request.method == "POST":
            sent.append(request.content.decode())
        return _response(request)

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        result = writer.create_pending_page(artifact, commit=True)
    assert result["status"] == "CREATED_PENDING_REVIEW"
    assert len(sent) == 2
    assert "PENDING_HUMAN_REVIEW" not in sent[0]  # Human-facing state only
    assert "QJAI_PUBLISH_READY" not in sent[0]
    assert "档案编号" in sent[0]
    # Internal review warnings remain in the artifact, not in publishable body blocks.
    assert artifact["qa"]["editorial_warnings"] == ["太阳能板屋顶位置无来源支持"]
    assert "太阳能板屋顶位置无来源支持" not in sent[0]
    assert "太阳能板屋顶位置无来源支持" in sent[1]
    assert "内部编辑备注" in sent[1]
    assert "QJAI_PUBLISH_READY" not in sent[1]
    assert result["review_note"] is not None


def test_duplicate_issue_rejected(case, model_response, model_review):
    artifact = build_artifact(case, "01", model_response, model_review)

    def handler(request):
        return _response(request, title="海外创业志 01|另一篇")

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        with pytest.raises(ProductionError, match="already exists"):
            writer.create_pending_page(artifact, commit=True)


def test_ambiguous_post_does_not_retry(case, model_response, model_review):
    artifact = build_artifact(case, "01", model_response, model_review)
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(1)
            raise httpx.ReadTimeout("ambiguous")
        return _response(request)

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        with pytest.raises(ProductionError, match="UNKNOWN"):
            writer.create_pending_page(artifact, commit=True)
    assert len(posts) == 1


def test_editorial_note_failure_does_not_retry_or_publish(case, model_response, model_review):
    artifact = build_artifact(case, "01", model_response, model_review)
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request.content.decode())
            if len(posts) == 2:
                raise httpx.ReadTimeout("ambiguous note write")
        return _response(request)

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        with pytest.raises(ProductionError, match="Editorial note outcome UNKNOWN"):
            writer.create_pending_page(artifact, commit=True)
    assert len(posts) == 2  # Article might exist; no automatic retry of either POST.
    assert "QJAI_PUBLISH_READY" not in posts[0]


def test_no_editorial_notes_does_not_create_sidecar(case, model_response, model_review):
    model_review["issues"] = []
    artifact = build_artifact(case, "01", model_response, model_review)
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(1)
        return _response(request)

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        result = writer.create_pending_page(artifact, commit=True)
    assert result["review_note"] is None
    assert len(posts) == 1


def test_markdown_blocks_have_short_rich_text(case, model_response):
    markdown = "# 标题\n\n" + "一" * 9200
    blocks = _to_blocks(markdown, story_id=case.id,
                        evidence_ids=[f.id for f in case.facts])
    assert max(len(p["text"]["content"]) for b in blocks
               for p in b[b["type"]]["rich_text"]) <= 1500




def test_e2e_commit_creates_isolated_container_then_pending_article(
    case, model_response, model_review
):
    import json

    from overseas_story_producer.notion import E2E_TEST_PARENT_TITLE

    artifact = build_artifact(case, "02", model_response, model_review)
    test_parent_id = "33333333-3333-4333-8333-333333333333"
    article_id = "44444444-4444-4444-8444-444444444444"
    gets = []
    posts = []

    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/children"):
            parent_id = request.url.path.split("/")[-2]
            gets.append(parent_id)
            return httpx.Response(200, json={"results": [], "has_more": False})
        if request.method == "POST" and request.url.path == "/v1/pages":
            payload = json.loads(request.content)
            parent_id = payload["parent"]["page_id"]
            posts.append(payload)
            if parent_id == PARENT_ID:
                return httpx.Response(200, json={
                    "id": test_parent_id,
                    "parent": {"type": "page_id", "page_id": PARENT_ID},
                    "url": "https://www.notion.so/m7-e2e-test-area",
                })
            return httpx.Response(200, json={
                "id": article_id,
                "parent": {"type": "page_id", "page_id": test_parent_id},
                "url": "https://www.notion.so/m7-e2e-article",
            })
        return httpx.Response(404)

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        result = writer.create_pending_page(artifact, commit=True, e2e_test=True)

    assert result["status"] == "CREATED_PENDING_REVIEW"
    assert result["page_id"] == article_id
    assert result["parent"] == test_parent_id
    assert result["e2e_test"] is True
    assert len(posts) == 3
    assert posts[0]["parent"]["page_id"] == PARENT_ID
    assert posts[0]["properties"]["title"]["title"][0]["text"]["content"] == E2E_TEST_PARENT_TITLE
    assert posts[1]["parent"]["page_id"] == test_parent_id
    assert posts[1]["properties"]["title"]["title"][0]["text"]["content"] == artifact["notion_title"]
    assert "QJAI_PUBLISH_READY" not in json.dumps(posts[1], ensure_ascii=False)
    assert "编辑备注" in posts[2]["properties"]["title"]["title"][0]["text"]["content"]
    assert "可压缩一个重复段落" not in json.dumps(posts[1], ensure_ascii=False)
    assert "可压缩一个重复段落" in json.dumps(posts[2], ensure_ascii=False)
    assert writer.parent_id == PARENT_ID


def test_failed_qa_diagnostic_is_written_only_to_isolated_test_area(
    case, model_response, model_review
):
    import json

    review = dict(model_review)
    review["unsupported_claims"] = ["未获支持的测试陈述"]
    review["hard_fail"] = True
    review["hard_fail_reason"] = "虚构关键收入及人生事件"
    artifact = build_artifact(case, "02", model_response, review)
    assert not artifact["qa"]["passes_automatic_checks"]
    test_parent_id = "33333333-3333-4333-8333-333333333333"
    diagnostic_id = "55555555-5555-4555-8555-555555555555"
    posts = []

    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/children"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        if request.method == "POST" and request.url.path == "/v1/pages":
            payload = json.loads(request.content)
            posts.append(payload)
            parent_id = payload["parent"]["page_id"]
            if parent_id == PARENT_ID:
                return httpx.Response(200, json={
                    "id": test_parent_id,
                    "parent": {"type": "page_id", "page_id": PARENT_ID},
                    "url": "https://www.notion.so/m7-e2e-test-area",
                })
            return httpx.Response(200, json={
                "id": diagnostic_id,
                "parent": {"type": "page_id", "page_id": test_parent_id},
                "url": "https://www.notion.so/m7-e2e-qa-diagnostic",
            })
        return httpx.Response(404)

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        result = writer.create_e2e_qa_diagnostic(
            artifact, run_id="42485803401", commit=True, e2e_test=True,
        )

    assert result["status"] == "CREATED_QA_FAILURE_DIAGNOSTIC"
    assert result["diagnostic_only"] is True
    assert result["parent"] == test_parent_id
    assert len(posts) == 2
    diagnostic_json = json.dumps(posts[1], ensure_ascii=False)
    assert posts[1]["parent"]["page_id"] == test_parent_id
    assert "QA诊断" in posts[1]["properties"]["title"]["title"][0]["text"]["content"]
    assert "未获支持的测试陈述" in diagnostic_json
    assert model_response["body_markdown"] not in diagnostic_json
    assert "QJAI_PUBLISH_READY" not in diagnostic_json
    assert "publish_ready" not in diagnostic_json
    assert writer.parent_id == PARENT_ID


def test_failed_qa_diagnostic_refuses_non_e2e_or_passing_artifacts(
    case, model_response, model_review
):
    writer = NotionDraftWriter("not-a-real-key", PARENT_ID,
                               transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with writer:
        passing = build_artifact(case, "02", model_response, model_review)
        with pytest.raises(ProductionError, match="failed QA gate"):
            writer.create_e2e_qa_diagnostic(
                passing, run_id="42485803401", commit=True, e2e_test=True,
            )
        failed_review = dict(model_review)
        failed_review["unsupported_claims"] = ["test claim"]
        failed_review["hard_fail"] = True
        failed_review["hard_fail_reason"] = "Fabricated major milestone"
        failed = build_artifact(case, "02", model_response, failed_review)
        with pytest.raises(ProductionError, match="explicit E2E commit"):
            writer.create_e2e_qa_diagnostic(
                failed, run_id="42485803401", commit=False, e2e_test=True,
            )


def test_e2e_test_mode_refuses_dry_run(case, model_response, model_review):
    artifact = build_artifact(case, "02", model_response, model_review)
    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(lambda request: httpx.Response(200))) as writer:
        with pytest.raises(ProductionError, match="requires an explicit commit"):
            writer.create_pending_page(artifact, e2e_test=True)
