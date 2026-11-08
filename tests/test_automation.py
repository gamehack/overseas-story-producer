import json
from uuid import uuid4

import httpx
import pytest

from overseas_story_producer import automation
from overseas_story_producer.core import ProductionError
from overseas_story_producer.notion import NotionDraftWriter


def test_inventory_reads_existing_story_and_human_ready():
    id_ = str(uuid4())
    parent = str(uuid4())
    case = str(uuid4())
    def handler(request):
        if request.url.path.endswith("/children"):
            return httpx.Response(200, json={"results": [
                {"type": "child_page", "id": id_,
                 "child_page": {"title": "海外创业志 01|人物冲突"}},
                {"type": "child_page", "id": str(uuid4()),
                 "child_page": {"title": "M7 编辑备注｜01"}},
            ], "has_more": False})
        return httpx.Response(200, json={"object": "page_markdown", "markdown":
            f"# 人物冲突\n\n档案编号：{case}；已引用 8 条事实。\n\nQJAI\\_PUBLISH\\_READY v=1 ...",
            "truncated": False, "unknown_block_ids": [], "warnings": []})
    with NotionDraftWriter("x", parent, transport=httpx.MockTransport(handler)) as writer:
        assert writer.production_inventory() == (2, {case}, 0)


def test_plan_skips_editorial_backlog_before_database_read(monkeypatch):
    class Writer:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def production_inventory(self): return (4, set(), 2)
    monkeypatch.setattr(automation, "NotionDraftWriter", Writer)
    monkeypatch.setattr(automation, "eligible_stories", lambda *_: pytest.fail("should not read DB"))
    assert automation.plan_next(database_url="x", notion_token="x", notion_parent_id="x")["status"] == "SKIPPED"


def test_plan_prioritizes_curated_unused_story(monkeypatch):
    used, top, preferred = [str(uuid4()) for _ in range(3)]
    class Writer:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def production_inventory(self): return (2, {used}, 0)
    monkeypatch.setattr(automation, "NotionDraftWriter", Writer)
    monkeypatch.setattr(automation, "eligible_stories", lambda *_: [(used, 100), (top, 99), (preferred, 80)])
    plan = automation.plan_next(database_url="x", notion_token="x", notion_parent_id="x",
                                priority_ids=preferred)
    assert plan["story_id"] == preferred and plan["issue_number"] == "02"


def test_batch_excludes_used_and_duplicate_sources_but_keeps_distinct_projects(monkeypatch):
    used, same_url, distinct_project, duplicate_project = [str(uuid4()) for _ in range(4)]
    class Writer:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def production_inventory(self): return (2, {used}, 0)
    monkeypatch.setattr(automation, "NotionDraftWriter", Writer)
    monkeypatch.setattr(automation, "eligible_stories", lambda *_: [
        (same_url, 95), (distinct_project, 90), (duplicate_project, 80), (used, 75)])
    monkeypatch.setattr(automation, "story_identities", lambda *_: {
        used: ("https://x.test/a", "hash-a", "onestory", "alice", "product-a"),
        same_url: ("https://x.test/a", "hash-b", "anotherstory", "alice", "product-a"),
        distinct_project: ("https://x.test/b", "hash-c", "differentstory", "alice", "product-b"),
        duplicate_project: ("https://x.test/b", "hash-c", "differentstory", "alice", "product-b"),
    })
    plan = automation.plan_batch(database_url="x", notion_token="x", notion_parent_id="x")
    assert [row["story_id"] for row in plan["candidates"]] == [distinct_project]
    assert plan["next_issue_number"] == "02"
    assert plan["duplicate_count"] == 3


def test_batch_refuses_unsigned_issue(monkeypatch):
    class Writer:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def production_inventory(self): return (3, set(), 1)
    monkeypatch.setattr(automation, "NotionDraftWriter", Writer)
    with pytest.raises(ProductionError, match="Unsigned"):
        automation.plan_batch(database_url="x", notion_token="x", notion_parent_id="x")


def test_same_founder_different_ventures_not_blanket_blocked():
    a = ("https://x.test/a", "a", "mystory", "alice", "venture-a")
    b = ("https://x.test/b", "b", "newstory", "alice", "venture-b")
    assert not automation.same_story(a, b)
