"""Explicit preview/submission CLI; no cron and no publish operation."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

from .core import (ProductionError, author_prompt, build_artifact, canonical_period,
                   reviewer_prompt, validate_artifact)
from .model import generate_json
from .notion import NotionDraftWriter
from .source import load_case


def _review_cooldown_seconds() -> float:
    # A successful author response can exhaust a provider's per-minute budget.
    # Delay the independent review, but never automatically retry a failed call.
    raw = os.getenv("OPENAI_COMPAT_REVIEW_COOLDOWN_SECONDS", "90").strip()
    try:
        seconds = float(raw)
    except ValueError:
        raise ProductionError("Invalid model review cooldown") from None
    if not math.isfinite(seconds) or not 0 <= seconds <= 600:
        raise ProductionError("Model review cooldown must be between 0 and 600 seconds")
    return seconds


def _model_timeout_seconds() -> float:
    # A separate, bounded value. No retries are performed on timeout.
    raw = os.getenv("OPENAI_COMPAT_TIMEOUT_SECONDS", "120").strip()
    try:
        seconds = float(raw)
    except ValueError:
        raise ProductionError("Invalid model timeout") from None
    if not math.isfinite(seconds) or not 10 <= seconds <= 300:
        raise ProductionError("Model timeout must be between 10 and 300 seconds")
    return seconds


def run(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="QJAI independent overseas-story producer")
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate", help="Generate a local pending-review artifact")
    g.add_argument("--story-id", required=True)
    g.add_argument("--period", required=True)
    g.add_argument("--output", type=Path, required=True)
    s = sub.add_parser("submit", help="Submit a pending story to Notion, not to publishers")
    s.add_argument("--input", type=Path, required=True)
    s.add_argument("--commit", action="store_true", help="Actually create an unapproved draft")
    s.add_argument("--e2e-test", action="store_true", help="Create the draft only inside the dedicated M7 E2E test area")
    v = sub.add_parser("inspect", help="Inspect artifact quality without external calls")
    v.add_argument("--input", type=Path, required=True)
    d = sub.add_parser("diagnose", help="Write failed QA details only to the isolated E2E Notion area")
    d.add_argument("--input", type=Path, required=True)
    d.add_argument("--run-id", required=True)
    d.add_argument("--commit", action="store_true")
    d.add_argument("--e2e-test", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "generate":
        period = canonical_period(args.period)
        review_cooldown = _review_cooldown_seconds()
        model_timeout = _model_timeout_seconds()
        case = load_case(os.getenv("DATABASE_URL", ""), args.story_id)
        system, prompt = author_prompt(case)
        allow_private_http = os.getenv("OPENAI_COMPAT_ALLOW_PRIVATE_HTTP", "false").strip().lower() in {"1", "true"}
        author_transport: dict[str, str] = {}
        reviewer_transport: dict[str, str] = {}
        try:
            model_result = generate_json(
                system, prompt,
                api_base=os.getenv("OPENAI_COMPAT_BASE_URL", ""),
                api_key=os.getenv("OPENAI_COMPAT_API_KEY", ""),
                model=os.getenv("OPENAI_COMPAT_MODEL", ""),
                allow_private_http=allow_private_http,
                timeout_seconds=model_timeout,
                transport_info=author_transport,
            )
        except ProductionError as exc:
            raise ProductionError(f"Author stage: {exc}") from None
        review_system, review_user = reviewer_prompt(case, model_result)
        if review_cooldown:
            time.sleep(review_cooldown)
        try:
            review_result = generate_json(
                review_system, review_user,
                api_base=os.getenv("OPENAI_COMPAT_BASE_URL", ""),
                api_key=os.getenv("OPENAI_COMPAT_API_KEY", ""),
                model=os.getenv("OPENAI_COMPAT_MODEL", ""),
                allow_private_http=allow_private_http,
                timeout_seconds=model_timeout,
                transport_info=reviewer_transport,
            )
        except ProductionError as exc:
            raise ProductionError(f"Reviewer stage: {exc}") from None
        item = build_artifact(case, period, model_result, review_result)
        # The output remains an artifact even when QA fails, for editor review.
        if args.output.exists():
            raise ProductionError("Refusing to overwrite existing artifact")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(item, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return {"status": "LOCAL_PENDING_REVIEW", "path": str(args.output),
                "qa": item["qa"], "notion_title": item["notion_title"],
                "model_transport": {
                    "author": author_transport.get("mode", "unknown"),
                    "reviewer": reviewer_transport.get("mode", "unknown"),
                }}

    try:
        if args.input.stat().st_size > 100_000:
            raise ProductionError("Artifact too large")
        item = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(item, dict):
            raise ValueError("Artifact must be object")
    except (OSError, ValueError):
        raise ProductionError("Cannot parse input artifact") from None
    if args.command == "diagnose":
        with NotionDraftWriter(os.getenv("NOTION_API_KEY", ""),
                               os.getenv("NOTION_PARENT_PAGE_ID", "")) as writer:
            return writer.create_e2e_qa_diagnostic(
                item, run_id=args.run_id, commit=args.commit, e2e_test=args.e2e_test,
            )
    validate_artifact(item)
    if args.command == "inspect":
        return {"status": "VALID_PENDING_ARTIFACT", "qa": item["qa"],
                "title": item["notion_title"]}
    with NotionDraftWriter(os.getenv("NOTION_API_KEY", ""),
                           os.getenv("NOTION_PARENT_PAGE_ID", "")) as writer:
        return writer.create_pending_page(item, commit=args.commit, e2e_test=args.e2e_test)


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(argv)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ProductionError as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

