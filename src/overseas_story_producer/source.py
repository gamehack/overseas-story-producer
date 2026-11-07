"""Read only from the existing overseas_stories Supabase Postgres schema."""
from __future__ import annotations

import hashlib
import json

import psycopg

from .core import Case, Fact, ProductionError, validate_case


def load_case(database_url: str, story_id: str) -> Case:
    if not database_url:
        raise ProductionError("DATABASE_URL is required")
    try:
        from uuid import UUID

        story_id = str(UUID(story_id))
    except ValueError:
        raise ProductionError("Invalid story UUID") from None
    try:
        # Repeatable-read SELECT-only transaction prevents writes even if a
        # mistakenly overprivileged credential is supplied. Deploy with a
        # separate SELECT-only database role as a second independent guard.
        with psycopg.connect(database_url, connect_timeout=10, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cur.execute("""
                    SELECT s.id::text, s.title, s.source_url,
                           COALESCE(s.body_markdown, ''), sl.title_zh,
                           sl.synopsis_zh, s.content_hash, sl.source_hash,
                           sl.model, sl.prompt_version
                    FROM overseas_stories.stories AS s
                    JOIN overseas_stories.story_localizations AS sl
                      ON sl.story_id = s.id AND sl.locale = 'zh-CN'
                    WHERE s.id = %s
                    """, (story_id,))
                row = cur.fetchone()
                if row is None:
                    raise ProductionError("Story or current Chinese localization missing")
                cur.execute("""
                    SELECT f.id::text, f.fact_type, f.value, lf.value_zh,
                           f.evidence, f.self_stated
                    FROM overseas_stories.facts AS f
                    LEFT JOIN overseas_stories.localized_facts AS lf
                      ON lf.fact_id = f.id AND lf.locale = 'zh-CN'
                    WHERE f.story_id = %s AND f.active = true
                    ORDER BY f.fact_type, f.evidence_hash, f.id
                    """, (story_id,))
                raw_facts = cur.fetchall()
    except ProductionError:
        raise
    except psycopg.Error:
        # Do not echo connection URIs, password fields, or DB exception strings.
        raise ProductionError("Read-only Supabase case query failed") from None
    # Recompute the collector's M4 source_hash using *all* active indexed facts
    # (including a fact whose translation is missing/stale). Avoid using
    # translations that were generated from an older version of the evidence.
    def sha(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    source_facts = [
        (f[0], sha(f"{f[1]}\n{f[2]}\n{f[4]}")) for f in raw_facts
    ]
    expected_hash = sha(json.dumps({
        "story_hash": row[6], "title": row[1], "model": row[8],
        "prompt_version": row[9], "facts": source_facts,
    }, sort_keys=True, ensure_ascii=False))
    if not row[7] or row[7] != expected_hash:
        raise ProductionError("Chinese localization is stale versus source facts")
    case = Case(
        id=row[0], title=row[1] or "", source_url=row[2] or "",
        body_excerpt=row[3] or "", title_zh=row[4] or "",
        synopsis_zh=row[5] or "",
        facts=tuple(Fact(id=f[0], kind=f[1] or "", original=f[2] or "",
                         chinese=f[3] or "", evidence=f[4] or "",
                         self_stated=bool(f[5])) for f in raw_facts),
    )
    validate_case(case)
    return case

