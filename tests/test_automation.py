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
