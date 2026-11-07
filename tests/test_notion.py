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
    artifact = build_artifact(case, "2026-11-09", model_response, model_review)
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
    artifact = build_artifact(case, "2026-11-09", model_response, model_review)
    sent = []

    def handler(request):
        if request.method == "POST":
            sent.append(request.content.decode())
        return _response(request)

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        result = writer.create_pending_page(artifact, commit=True)
    assert result["status"] == "CREATED_PENDING_REVIEW"
    assert len(sent) == 1
    assert "PENDING_HUMAN_REVIEW" not in sent[0]  # Human-facing state only
    assert "QJAI_PUBLISH_READY" not in sent[0]
    assert "档案编号" in sent[0]


def test_duplicate_period_rejected(case, model_response, model_review):
    artifact = build_artifact(case, "2026-11-09", model_response, model_review)

    def handler(request):
        return _response(request, title="2026-11-09｜海外创业志｜另一篇")

    with NotionDraftWriter("not-a-real-key", PARENT_ID,
                           transport=httpx.MockTransport(handler)) as writer:
        with pytest.raises(ProductionError, match="already exists"):
            writer.create_pending_page(artifact, commit=True)


def test_ambiguous_post_does_not_retry(case, model_response, model_review):
    artifact = build_artifact(case, "2026-11-09", model_response, model_review)
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


def test_markdown_blocks_have_short_rich_text(case, model_response):
    markdown = "# 标题\n\n" + "一" * 9200
    blocks = _to_blocks(markdown, story_id=case.id,
                        evidence_ids=[f.id for f in case.facts])
    assert max(len(p["text"]["content"]) for b in blocks
               for p in b[b["type"]]["rich_text"]) <= 1500

