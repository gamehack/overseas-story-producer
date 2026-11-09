"""Explicit preview/submission CLI; no cron and no publish operation."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

from .core import (ProductionError, author_prompt, build_artifact, canonical_issue_number,
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


def generate_article(story_id: str, issue_number: str) -> tuple[dict, dict[str, str]]:
    """Author + independent review, with at most one retry for malformed model JSON.

    A retry cannot publish or write to Notion. Rate limits, transport errors,
    timeouts and invalid source data remain terminal for this case/run.
    """
    issue_number = canonical_issue_number(issue_number)
    review_cooldown = _review_cooldown_seconds()
    model_timeout = _model_timeout_seconds()
    case = load_case(os.getenv("DATABASE_URL", ""), story_id)
    allow_private_http = os.getenv("OPENAI_COMPAT_ALLOW_PRIVATE_HTTP", "false").strip().lower() in {"1", "true"}
    author_transport: dict[str, str] = {}
    reviewer_transport: dict[str, str] = {}

    def stage_completion(stage: str, system_text: str, user_text: str,
                         transport: dict[str, str]) -> dict:
        for attempt in range(2):
            try:
                return generate_json(
                    system_text, user_text,
                    api_base=os.getenv("OPENAI_COMPAT_BASE_URL", ""),
                    api_key=os.getenv("OPENAI_COMPAT_API_KEY", ""),
                    model=os.getenv("OPENAI_COMPAT_MODEL", ""),
                    allow_private_http=allow_private_http,
                    timeout_seconds=model_timeout,
                    transport_info=transport,
                )
            except ProductionError as exc:
                if str(exc) == "Model returned malformed JSON" and attempt == 0:
                    # Cool down before a single fresh generation; never attempt
                    # lossy repairs on truncated or otherwise incomplete JSON.
                    if review_cooldown:
                        time.sleep(review_cooldown)
                    continue
                raise ProductionError(f"{stage} stage: {exc}") from None
        raise AssertionError("unreachable model retry state")

    system, prompt = author_prompt(case)
    model_result = stage_completion("Author", system, prompt, author_transport)
    review_system, review_user = reviewer_prompt(case, model_result)
    if review_cooldown:
        time.sleep(review_cooldown)
    review_result = stage_completion("Reviewer", review_system, review_user, reviewer_transport)
    return build_artifact(case, issue_number, model_result, review_result), {
        "author": author_transport.get("mode", "unknown"),
        "reviewer": reviewer_transport.get("mode", "unknown"),
    }


def run_batch(*, commit: bool, max_minutes: float) -> dict:
    """Write every eligible article in score order with fail-closed checkpoints.

    On restart, Notion's verified source UUIDs and serials are the durable
    checkpoint. Do not write local artifacts or manuscripts to Actions logs.
    """
    from .automation import plan_batch
    plan = plan_batch(database_url=os.getenv("DATABASE_URL", ""),
                      notion_token=os.getenv("NOTION_API_KEY", ""),
                      notion_parent_id=os.getenv("NOTION_PARENT_PAGE_ID", ""),
                      priority_ids=os.getenv("M7_PRIORITY_STORY_IDS", ""))
    base = {"status": plan["status"], "eligible_count": plan["eligible_count"],
            "remaining_count": plan["remaining_count"],
            "duplicate_count": plan["duplicate_count"],
            "next_issue_number": plan["next_issue_number"]}
    if not commit or not plan["candidates"]:
        return {**base, "external_write": False}
    started = time.monotonic()
    issue = int(plan["next_issue_number"])
    completed = 0
    qa_rejected = 0
    source_rejected = 0
    model_rejected = 0
    # Do not append failure records or full generated manuscripts to public logs.
    with NotionDraftWriter(os.getenv("NOTION_API_KEY", ""),
                           os.getenv("NOTION_PARENT_PAGE_ID", "")) as notion:
        for candidate in plan["candidates"]:
            if time.monotonic() - started >= max_minutes * 60:
                return {**base, "status": "PARTIAL_TIME_BUDGET", "created": completed,
                        "qa_rejected": qa_rejected, "source_rejected": source_rejected,
                        "model_rejected": model_rejected,
                        "unattempted": len(plan["candidates"]) - completed - qa_rejected - source_rejected - model_rejected,
                        "external_write": True}
            if issue > 99999:
                raise ProductionError("Series issue number exhausted")
            try:
                artifact, _ = generate_article(candidate["story_id"], f"{issue:02d}")
            except ProductionError as exc:
                # An individual localization may have become stale after the
                # eligibility query. Skip it without consuming a serial; do not
                # keep calling a failing model gateway or Notion API.
                if str(exc).startswith(("Chinese localization is stale",
                                         "Story or current Chinese localization missing",
                                         "Original evidence URL must use HTTPS",
                                         "Chinese localized title and synopsis required",
                                         "Insufficient original evidence",
                                         "Duplicate fact UUID",
                                         "Missing original/translated fact/evidence")):
                    source_rejected += 1
                    continue
                if str(exc) in ("Author stage: Model returned malformed JSON",
                                "Reviewer stage: Model returned malformed JSON"):
                    # Quarantine only this source record for this run.
                    # The next scheduled plan will include it again, and already
                    # signed Notion pages remain stable checkpoints.
                    model_rejected += 1
                    continue
                raise
            if not artifact["qa"]["passes_automatic_checks"]:
                qa_rejected += 1
                continue
            receipt = notion.create_pending_page(artifact, commit=True, auto_ready=True)
            if receipt["status"] != "CREATED_AUTO_READY" or not receipt["content_sha256"]:
                raise ProductionError("Automatic READY receipt is incomplete; stop production")
            completed += 1
            issue += 1
    return {**base, "status": "PARTIAL_MODEL_REJECTED" if model_rejected else "COMPLETED",
            "created": completed,
            "qa_rejected": qa_rejected, "source_rejected": source_rejected,
            "model_rejected": model_rejected, "unattempted": 0,
            "last_issue_number": f"{issue - 1:02d}" if completed else None,
            "external_write": True}


def run(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="QJAI independent overseas-story producer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="Read-only inventory of all eligible stories for weekly batch production")
    b = sub.add_parser("batch", help="Batch generate every eligible story; automatic READY after QA")
    b.add_argument("--commit", action="store_true", help="Create signed Notion inventory; default is read-only")
    b.add_argument("--max-minutes", type=float, default=320.0,
                   help="Bound a single GitHub Actions run; resume safely on the next run")
    g = sub.add_parser("generate", help="Generate a local pending-review artifact")
    g.add_argument("--story-id", required=True)
    g.add_argument("--issue-number", required=True, help="Publication issue, e.g. 01; never a date")
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

    if args.command == "plan":
        return run_batch(commit=False, max_minutes=0)

    if args.command == "batch":
        if not math.isfinite(args.max_minutes) or not 1 <= args.max_minutes <= 340:
            raise ProductionError("Batch time budget must be 1..340 minutes")
        return run_batch(commit=args.commit, max_minutes=args.max_minutes)

    if args.command == "generate":
        item, model_transport = generate_article(args.story_id, args.issue_number)
        # The output remains an artifact even when QA fails, for editor review.
        if args.output.exists():
            raise ProductionError("Refusing to overwrite existing artifact")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(item, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return {"status": "LOCAL_PENDING_REVIEW", "path": str(args.output),
                "qa": item["qa"], "notion_title": item["notion_title"],
                "model_transport": model_transport}

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

