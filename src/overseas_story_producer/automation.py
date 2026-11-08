"""Bounded read-only selection for normal, human-reviewed M7 production."""
from __future__ import annotations

import os
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
                    WHERE ec.review_status='new' AND ec.score >= 70
                    GROUP BY ec.story_id, ec.score
                    HAVING COUNT(DISTINCT f.id) >= 5
                    ORDER BY ec.score DESC, ec.story_id
                    LIMIT 300
                """)
                return [(str(UUID(row[0])), row[1]) for row in cur.fetchall()]
    except psycopg.Error:
        raise ProductionError("Read-only candidate selection failed") from None


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
