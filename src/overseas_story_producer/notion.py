"""Submit only unapproved Notion child pages, with fail-closed idempotency."""
from __future__ import annotations

from uuid import UUID

import httpx

from .core import ProductionError, canonical_issue_number, validate_artifact


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

    def _create_editorial_notes(self, artifact: dict, article_id: str) -> dict | None:
        """Keep review notes adjacent to, but separate from, the publishable article."""
        notes = artifact.get("qa", {}).get("editorial_issues", [])
        if not notes:
            return None
        if (not isinstance(notes, list) or len(notes) > 20 or
                not all(isinstance(note, str) and 0 < len(note) <= 1000 for note in notes)):
            raise ProductionError("Editorial review notes exceed safe bounds")
        title = f"M7 编辑备注｜{artifact['issue_number']}｜{article_id}"
        blocks = [
            _block("heading_1", "内部编辑备注｜不是文章正文"),
            _block("paragraph", "仅供人工审阅，不得添加 READY、进入 CMS 或微信公众号发布流程。"),
            _block("paragraph", f"关联待审文章 ID：{article_id}"),
            _block("paragraph", f"独立审稿：{artifact['qa']['editorial_score']}/100"),
        ]
        blocks.extend(_block("paragraph", f"编辑提醒 {i}：{note}")
                      for i, note in enumerate(notes, 1))
        payload = {
            "parent": {"type": "page_id", "page_id": self.parent_id},
            "properties": {"title": {"type": "title", "title": _rich(title)}},
            "children": blocks,
        }
        try:
            response = self.client.post("/pages", json=payload)
            if response.status_code not in (200, 201) or len(response.content) > 100_000:
                raise ProductionError("Editorial note creation failed after article creation; outcome may be unknown")
            page = response.json()
            note_id = str(UUID(page["id"]))
            parent = page.get("parent", {})
            if (parent.get("type") != "page_id" or
                    str(UUID(parent.get("page_id", ""))) != self.parent_id):
                raise ValueError("Wrong note parent")
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            # The article already exists; never retry an uncertain Notion write.
            raise ProductionError("Editorial note outcome UNKNOWN after article creation; inspect Notion before retrying") from None
        return {"page_id": note_id, "url": page.get("url", ""), "title": title}


    def create_e2e_qa_diagnostic(self, artifact: dict, *, run_id: str,
                                 commit: bool = False,
                                 e2e_test: bool = False) -> dict:
        """Write QA failure details only to the isolated E2E area, never as an article."""
        if not commit or not e2e_test:
            raise ProductionError("QA diagnostics require explicit E2E commit mode")
        if not isinstance(artifact, dict):
            raise ProductionError("Invalid QA diagnostic artifact")
        if artifact.get("status") != "PENDING_HUMAN_REVIEW" or artifact.get("publish_ready") is not False:
            raise ProductionError("QA diagnostic requires a non-publishable pending artifact")
        qa = artifact.get("qa")
        review = artifact.get("editorial_review")
        if not isinstance(qa, dict) or qa.get("passes_automatic_checks") is not False:
            raise ProductionError("QA diagnostic is allowed only for a failed QA gate")
        if not isinstance(review, dict):
            raise ProductionError("Independent review details are missing")
        claims = review.get("unsupported_claims")
        issues = review.get("issues")
        hard_fail = review.get("hard_fail")
        if (not isinstance(claims, list) or not all(isinstance(x, str) and 0 < len(x) <= 1000 for x in claims)
                or len(claims) > 10
                or not isinstance(issues, list) or not all(isinstance(x, str) and 0 < len(x) <= 500 for x in issues)
                or len(issues) > 20 or type(hard_fail) is not bool):
            raise ProductionError("QA diagnostic fields failed validation")
        issue_number = canonical_issue_number(artifact.get("issue_number", ""))
        try:
            story_id = str(UUID(str(artifact.get("story_id", ""))))
        except (ValueError, TypeError, AttributeError):
            raise ProductionError("Invalid story ID in QA diagnostic") from None
        if not isinstance(run_id, str) or not run_id.isdigit() or len(run_id) > 20:
            raise ProductionError("Invalid GitHub run ID")
        auto_issues = qa.get("issues", [])
        if not isinstance(auto_issues, list) or not all(isinstance(x, str) and len(x) <= 200 for x in auto_issues):
            raise ProductionError("Invalid structural QA issue list")
        score = qa.get("score")
        editorial_score = qa.get("editorial_score")
        if (type(score) is not int or not 0 <= score <= 100
                or type(editorial_score) is not int or not 0 <= editorial_score <= 100):
            raise ProductionError("Invalid QA score in diagnostic artifact")
        title = f"E2E QA诊断｜{issue_number}｜run-{run_id}"
        blocks = [
            _block("heading_1", "自动化质检未通过｜仅供排障，不是文章草稿"),
            _block("paragraph", "状态：QA_FAILED_DIAGNOSTIC_ONLY。此页不是待发布文章，不得进入 READY、CMS 或微信公众号发布流程。"),
            _block("paragraph", f"GitHub Actions Run：{run_id}"),
            _block("paragraph", f"案例 UUID：{story_id}"),
            _block("paragraph", f"来源：{str(artifact.get('original_url', ''))[:1000]}"),
            _block("paragraph", f"结构质检：{score}/100；结构问题数：{len(auto_issues)}"),
            _block("paragraph", f"独立审稿：{editorial_score}/100；审稿问题数：{len(issues)}；未获支持声明数：{len(claims)}；Hard Fail：{hard_fail}"),
        ]
        blocks.extend(_block("paragraph", f"结构问题：{issue}") for issue in auto_issues)
        blocks.extend(_block("paragraph", f"审稿意见：{issue}") for issue in issues)
        blocks.extend(_block("paragraph", f"未获证据支持的声明 {index}: {claim}")
                      for index, claim in enumerate(claims, start=1))
        if len(blocks) > 40:
            raise ProductionError("QA diagnostic exceeds Notion child block limit")
        original_parent = self.parent_id
        try:
            self.parent_id = self._ensure_e2e_test_parent()
            self._ensure_unique(title)
            payload = {
                "parent": {"type": "page_id", "page_id": self.parent_id},
                "properties": {"title": {"type": "title", "title": _rich(title)}},
                "children": blocks,
            }
            try:
                response = self.client.post("/pages", json=payload)
                if response.status_code not in (200, 201) or len(response.content) > 200_000:
                    raise ProductionError("Notion QA diagnostic creation failed; outcome may be unknown")
                page = response.json()
                page_id = str(UUID(page["id"]))
                parent = page.get("parent", {})
                if (parent.get("type") != "page_id" or
                        str(UUID(parent.get("page_id", ""))) != self.parent_id):
                    raise ValueError("Wrong QA diagnostic parent")
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                raise ProductionError("Notion QA diagnostic outcome UNKNOWN; inspect the M7 E2E test area before retrying") from None
            return {"status": "CREATED_QA_FAILURE_DIAGNOSTIC",
                    "page_id": page_id, "url": page.get("url", ""),
                    "title": title, "parent": self.parent_id,
                    "e2e_test": True, "diagnostic_only": True}
        finally:
            self.parent_id = original_parent


    def create_pending_page(self, artifact: dict, *, commit: bool = False,
                            e2e_test: bool = False) -> dict:
        validate_artifact(artifact)
        if e2e_test and not commit:
            raise ProductionError("E2E test mode requires an explicit commit to the isolated test area")
        issue_number = artifact["issue_number"]
        prefix = f"海外创业志 {issue_number}|"
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
            review_note = self._create_editorial_notes(artifact, str(page["id"]))
            return {"status": "CREATED_PENDING_REVIEW", "page_id": page["id"],
                    "url": page.get("url", ""), "title": artifact["notion_title"],
                    "parent": self.parent_id, "e2e_test": e2e_test,
                    "review_note": review_note}
        finally:
            self.parent_id = original_parent

