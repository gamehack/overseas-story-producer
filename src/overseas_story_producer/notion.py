"""Submit only unapproved Notion child pages, with fail-closed idempotency."""
from __future__ import annotations

from uuid import UUID

import httpx

from .core import ProductionError, validate_artifact


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
E2E_TEST_PARENT_TITLE = "M7 自动化验收测试区"


def _rich(value: str) -> list[dict]:
    # Notion text fields must fit within 2000 Unicode characters.
    return [{"type": "text", "text": {"content": value[i:i + 1500]}}
            for i in range(0, len(value), 1500)] or [
                {"type": "text", "text": {"content": ""}}]


def _block(kind: str, content: str) -> dict:
    return {"object": "block", "type": kind,
            kind: {"rich_text": _rich(content)}}


def _to_blocks(markdown: str, *, story_id: str, evidence_ids: list[str]) -> list[dict]:
    if len(markdown) > 18_000:
        raise ProductionError("Article exceeds Notion safety bound")
    blocks: list[dict] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            joined = "\n".join(buffer).strip()
            if joined:
                blocks.append(_block("paragraph", joined))
            buffer.clear()

    for line in markdown.splitlines():
        if not line.strip():
            flush()
        elif line.startswith("# "):
            flush()
            blocks.append(_block("heading_1", line[2:].strip()))
        elif line.startswith("## "):
            flush()
            blocks.append(_block("heading_2", line[3:].strip()))
        elif line.startswith("### "):
            flush()
            blocks.append(_block("heading_3", line[4:].strip()))
        else:
            buffer.append(line)
    flush()
    # A concise, reader-safe traceability footer: avoid writing internal
    # lifecycle labels that could leak into a subsequently published article.
    blocks.extend([
        _block("paragraph", f"档案编号：{story_id}；已引用 {len(evidence_ids)} 条原始事实。"),
    ])
    if len(blocks) > 90:
        raise ProductionError("Article exceeds Notion child block limit")
    return blocks


class NotionDraftWriter:
    def __init__(self, token: str, parent_page_id: str, *,
                 transport: httpx.BaseTransport | None = None):
        if not token:
            raise ProductionError("NOTION_API_KEY is required")
        try:
            self.parent_id = str(UUID(parent_page_id))
        except (ValueError, AttributeError, TypeError):
            raise ProductionError("NOTION_PARENT_PAGE_ID must be a UUID") from None
        self.client = httpx.Client(
            base_url=NOTION_API, timeout=30, follow_redirects=False,
            transport=transport,
            headers={"Authorization": "Bearer " + token,
                     "Notion-Version": NOTION_VERSION,
                     "Content-Type": "application/json"},
        )

    def __enter__(self) -> "NotionDraftWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def _get(self, path: str, params: dict) -> dict:
        try:
            response = self.client.get(path, params=params)
            if response.status_code != 200 or len(response.content) > 600_000:
                raise ProductionError("Cannot safely inspect existing Notion pages")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Notion JSON object required")
            return data
        except (httpx.HTTPError, ValueError):
            raise ProductionError("Notion read failure") from None

    def _find_child_page(self, parent_id: str, title: str) -> str | None:
        cursor: str | None = None
        seen: set[str] = set()
        matches: list[str] = []
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get(f"/blocks/{parent_id}/children", params)
            children = data.get("results")
            if not isinstance(children, list) or not isinstance(data.get("has_more"), bool):
                raise ProductionError("Incomplete Notion pagination")
            for child in children:
                if not isinstance(child, dict):
                    raise ProductionError("Invalid Notion child block")
                if child.get("type") == "child_page":
                    child_title = (child.get("child_page") or {}).get("title", "")
                    if not isinstance(child_title, str):
                        raise ProductionError("Invalid Notion child page title")
                    if child_title == title:
                        try:
                            matches.append(str(UUID(child.get("id", ""))))
                        except (ValueError, TypeError, AttributeError):
                            raise ProductionError("Invalid Notion child page ID") from None
            if not data["has_more"]:
                break
            cursor = data.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ProductionError("Notion pagination cannot be trusted")
            seen.add(cursor)
        if len(matches) > 1:
            raise ProductionError("Multiple M7 E2E test containers found; refusing ambiguous write")
        return matches[0] if matches else None

    def _ensure_e2e_test_parent(self) -> str:
        existing = self._find_child_page(self.parent_id, E2E_TEST_PARENT_TITLE)
        if existing:
            return existing
        payload = {
            "parent": {"type": "page_id", "page_id": self.parent_id},
            "properties": {"title": {"type": "title", "title": _rich(E2E_TEST_PARENT_TITLE)}},
        }
        try:
            response = self.client.post("/pages", json=payload)
            if response.status_code not in (200, 201) or len(response.content) > 100_000:
                raise ProductionError("Notion E2E test container creation failed; outcome may be unknown")
            page = response.json()
            page_id = str(UUID(page["id"]))
            parent = page.get("parent", {})
            if (parent.get("type") != "page_id" or
                    str(UUID(parent.get("page_id", ""))) != self.parent_id):
                raise ValueError("Wrong test container parent")
            return page_id
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            # A timed-out POST may have succeeded; never retry automatically.
            raise ProductionError("Notion E2E container outcome UNKNOWN; inspect the 07 root before retrying") from None

    def _ensure_unique(self, prefix: str) -> None:
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get(f"/blocks/{self.parent_id}/children", params)
            blocks = data.get("results")
            if not isinstance(blocks, list) or not isinstance(data.get("has_more"), bool):
                raise ProductionError("Incomplete Notion pagination")
            for child in blocks:
                if not isinstance(child, dict):
                    raise ProductionError("Invalid Notion child block")
                if child.get("type") == "child_page":
                    existing = (child.get("child_page") or {}).get("title", "")
                    if not isinstance(existing, str):
                        raise ProductionError("Invalid Notion child page title")
                    if existing.startswith(prefix):
                        raise ProductionError("Period already exists in Notion; refusing duplicate")
            if not data["has_more"]:
                return
            cursor = data.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ProductionError("Notion pagination cannot be trusted")
            seen.add(cursor)

    def create_pending_page(self, artifact: dict, *, commit: bool = False,
                            e2e_test: bool = False) -> dict:
        validate_artifact(artifact)
        if e2e_test and not commit:
            raise ProductionError("E2E test mode requires an explicit commit to the isolated test area")
        period = artifact["period"]
        prefix = f"{period}｜海外创业志｜"
        blocks = _to_blocks(artifact["body_markdown"],
                            story_id=artifact["story_id"],
                            evidence_ids=artifact["evidence_ids"])
        original_parent = self.parent_id
        try:
            if e2e_test:
                self.parent_id = self._ensure_e2e_test_parent()
            self._ensure_unique(prefix)
            if not commit:
                return {"status": "DRY_RUN", "title": artifact["notion_title"],
                        "blocks": len(blocks), "parent": self.parent_id}
            payload = {
                "parent": {"type": "page_id", "page_id": self.parent_id},
                "properties": {"title": {"type": "title", "title": _rich(artifact["notion_title"])}},
                "children": blocks,
            }
            try:
                response = self.client.post("/pages", json=payload)
                if response.status_code not in (200, 201) or len(response.content) > 300_000:
                    raise ProductionError("Notion creation failed; outcome may be unknown, inspect manually")
                page = response.json()
                UUID(str(page["id"]))
                parent = page.get("parent", {})
                if (parent.get("type") != "page_id" or
                        str(UUID(parent.get("page_id", ""))) != self.parent_id):
                    raise ValueError("Wrong parent")
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                # Never auto-retry a POST; an ambiguous response must be inspected.
                target = "M7 E2E test area" if e2e_test else "the 07 root"
                raise ProductionError(f"Notion outcome UNKNOWN; inspect {target} before retrying") from None
            return {"status": "CREATED_PENDING_REVIEW", "page_id": page["id"],
                    "url": page.get("url", ""), "title": artifact["notion_title"],
                    "parent": self.parent_id, "e2e_test": e2e_test}
        finally:
            self.parent_id = original_parent

