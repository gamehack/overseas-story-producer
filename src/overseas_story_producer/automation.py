"""Bounded read-only selection for normal, human-reviewed M7 production."""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import psycopg

from .core import ProductionError
from .notion import NotionDraftWriter


def eligible_stories(database_url: str) -> list[tuple[str, int]]:
    if not database_url:
        raise ProductionError("DATABASE_URL is required")
    try:
        with psycopg.connect(database_url, connect_timeout=10, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cur.execute("""
                    SELECT ec.story_id::text, ec.score
                    FROM overseas_stories.editorial_candidates ec
                    JOIN overseas_stories.story_localizations sl
                      ON sl.story_id=ec.story_id AND sl.locale='zh-CN'
                    JOIN overseas_stories.facts f
                      ON f.story_id=ec.story_id AND f.active=true
                    JOIN overseas_stories.fact_localizations fl
                      ON fl.fact_id=f.id AND fl.locale='zh-CN'
                    WHERE ec.review_status IN ('new', 'shortlisted', 'review_requested')
                      AND ec.score >= 70
                    GROUP BY ec.story_id, ec.score
                    HAVING COUNT(DISTINCT f.id) >= 5
                    ORDER BY ec.score DESC, ec.story_id
                """)
                return [(str(UUID(row[0])), row[1]) for row in cur.fetchall()]
    except psycopg.Error:
        raise ProductionError("Read-only candidate selection failed") from None


def story_identities(database_url: str, story_ids: set[str]) -> dict[str, tuple[str, str, str, str, str]]:
    """Fetch identity evidence, including previously used stories, for conservative dedupe.

    A person alone is deliberately not a duplicate identity: founders can have
    multiple genuinely different ventures and stories.
    """
    if not story_ids:
        return {}
    try:
        with psycopg.connect(database_url, connect_timeout=10, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cur.execute("""
                    SELECT s.id::text, s.source_url, s.content_hash, s.title,
                           coalesce(string_agg(DISTINCT lower(trim(p.name)), ','), ''),
                           coalesce(string_agg(DISTINCT lower(trim(pr.display_name)), ','), '')
                    FROM overseas_stories.stories s
                    LEFT JOIN overseas_stories.story_people sp ON sp.story_id=s.id
                    LEFT JOIN overseas_stories.people p ON p.id=sp.person_id
                    LEFT JOIN overseas_stories.projects pr ON pr.story_id=s.id AND pr.active=true
                    WHERE s.id = ANY(%s::uuid[])
                    GROUP BY s.id
                """, (sorted(story_ids),))
                rows = cur.fetchall()
    except psycopg.Error:
        raise ProductionError("Read-only story identity query failed") from None

    def norm(value: str) -> str:
        return re.sub(r"[^\w]+", "", (value or "").casefold())

    def canonical_url(raw: str) -> str:
        if not raw:
            return ""
        url = urlsplit(raw)
        return urlunsplit((url.scheme.lower(), url.netloc.lower(),
                           url.path.rstrip("/").lower(), "", ""))

    return {str(UUID(id_)): (canonical_url(url or ""), hash_ or "",
                             norm(title or ""), norm(people or ""), norm(projects or ""))
            for id_, url, hash_, title, people, projects in rows}


def same_story(a: tuple[str, str, str, str, str],
               b: tuple[str, str, str, str, str]) -> bool:
    url_a, hash_a, title_a, people_a, projects_a = a
    url_b, hash_b, title_b, people_b, projects_b = b
    if url_a and url_a == url_b:
        return True
    if hash_a and hash_a == hash_b:
        return True
    if not people_a or people_a != people_b:
        return False
    # The same founder can have several distinct ventures. Require a matching
    # project plus a similar story title, or near-identical titles without one.
    similarity = SequenceMatcher(None, title_a, title_b).ratio() if title_a and title_b else 0
    return bool(projects_a and projects_a == projects_b and similarity >= .75) or similarity >= .92


def plan_batch(*, database_url: str, notion_token: str, notion_parent_id: str,
               priority_ids: str = "") -> dict:
    """Plan every eligible, as-yet-unwritten story; no human-review backlog cap."""
    with NotionDraftWriter(notion_token, notion_parent_id) as notion:
        serial, used, unsigned = notion.production_inventory()
    if unsigned:
        # Any earlier unsigned serial blocks the daily publishers' FIFO order.
        raise ProductionError("Unsigned production issue requires safe reconciliation")
    candidates = eligible_stories(database_url)
    identities = story_identities(database_url, {id_ for id_, _ in candidates} | used)
    if any(id_ not in identities for id_, _ in candidates):
        raise ProductionError("Missing candidate identity; refusing incomplete dedupe")
    priority: list[str] = []
    for raw in priority_ids.split(","):
        if raw.strip():
            try:
                priority.append(str(UUID(raw.strip())))
            except ValueError:
                raise ProductionError("Invalid M7 priority story ID") from None
    ordered = sorted(candidates, key=lambda pair: (
        priority.index(pair[0]) if pair[0] in priority else len(priority), -pair[1], pair[0]))
    existing = [identities[id_] for id_ in used if id_ in identities]
    chosen: list[dict] = []
    duplicates: list[str] = []
    for id_, score in ordered:
        if id_ in used or any(same_story(identities[id_], earlier) for earlier in existing):
            duplicates.append(id_)
            continue
        chosen.append({"story_id": id_, "score": score})
        existing.append(identities[id_])
    return {"status": "PLANNED" if chosen else "SKIPPED", "next_issue_number": f"{serial:02d}",
            "candidates": chosen, "eligible_count": len(candidates),
            "already_used_count": len(used), "duplicate_count": len(duplicates),
            "remaining_count": len(chosen)}


def plan_next(*, database_url: str, notion_token: str, notion_parent_id: str,
              priority_ids: str = "", max_pending: int = 2) -> dict:
    with NotionDraftWriter(notion_token, notion_parent_id) as notion:
        serial, used, pending = notion.production_inventory()
    if pending >= max_pending:
        return {"status": "SKIPPED", "reason": "Editorial backlog limit reached",
                "pending_review": pending}
    candidates = [(id_, score) for id_, score in eligible_stories(database_url) if id_ not in used]
    if not candidates:
        return {"status": "SKIPPED", "reason": "No eligible unused localized story"}
    preferred: list[str] = []
    for raw in priority_ids.split(","):
        if raw.strip():
            try:
                preferred.append(str(UUID(raw.strip())))
            except ValueError:
                raise ProductionError("Invalid M7 priority story ID") from None
    ordered = sorted(candidates, key=lambda pair: (preferred.index(pair[0]) if pair[0] in preferred else len(preferred), -pair[1], pair[0]))
    return {"status": "PLANNED", "story_id": ordered[0][0],
            "issue_number": f"{serial:02d}", "candidate_score": ordered[0][1],
            "pending_review": pending}
