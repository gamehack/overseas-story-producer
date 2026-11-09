from uuid import uuid4

from overseas_story_producer import automation, cli


def test_batch_continues_after_quality_rejection_without_serial_gap(monkeypatch):
    ids = [str(uuid4()) for _ in range(3)]
    monkeypatch.setattr(automation, "plan_batch", lambda **_: {
        "status": "PLANNED", "eligible_count": 3, "remaining_count": 3,
        "duplicate_count": 0, "next_issue_number": "02",
        "candidates": [{"story_id": id_} for id_ in ids],
    })
    requested = []
    def generate(story_id, issue):
        requested.append((story_id, issue))
        return ({"qa": {"passes_automatic_checks": story_id != ids[1]},
                 "source_key": f"overseas-stories:{issue}"}, {})
    monkeypatch.setattr(cli, "generate_article", generate)
    submitted = []
    class Writer:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def create_pending_page(self, artifact, *, commit, auto_ready):
            assert commit and auto_ready
            submitted.append(artifact["source_key"])
            return {"status": "CREATED_AUTO_READY", "content_sha256": "a" * 64}
    monkeypatch.setattr(cli, "NotionDraftWriter", Writer)
    result = cli.run_batch(commit=True, max_minutes=100)
    assert requested == [(ids[0], "02"), (ids[1], "03"), (ids[2], "03")]
    assert submitted == ["overseas-stories:02", "overseas-stories:03"]
    assert result["created"] == 2 and result["qa_rejected"] == 1
    assert result["last_issue_number"] == "03"


def test_batch_read_only_never_invokes_generation(monkeypatch):
    monkeypatch.setattr(automation, "plan_batch", lambda **_: {
        "status": "PLANNED", "eligible_count": 1, "remaining_count": 1,
        "duplicate_count": 0, "next_issue_number": "05",
        "candidates": [{"story_id": str(uuid4())}],
    })
    monkeypatch.setattr(cli, "generate_article", lambda *_: 1 / 0)
    result = cli.run_batch(commit=False, max_minutes=0)
    assert result["remaining_count"] == 1
    assert result["external_write"] is False


def test_batch_quarantines_persistently_malformed_model_json_without_consuming_issue(monkeypatch):
    from overseas_story_producer.core import ProductionError
    ids = [str(uuid4()) for _ in range(3)]
    monkeypatch.setattr(automation, "plan_batch", lambda **_: {
        "status": "PLANNED", "eligible_count": 3, "remaining_count": 3,
        "duplicate_count": 0, "next_issue_number": "51",
        "candidates": [{"story_id": id_} for id_ in ids],
    })
    called = []
    def generate(story_id, issue):
        called.append((story_id, issue))
        if story_id == ids[1]:
            raise ProductionError("Author stage: Model returned malformed JSON")
        return ({"qa": {"passes_automatic_checks": True},
                 "source_key": f"overseas-stories:{issue}"}, {})
    monkeypatch.setattr(cli, "generate_article", generate)
    submitted = []
    class Writer:
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def create_pending_page(self, artifact, *, commit, auto_ready):
            assert commit and auto_ready
            submitted.append(artifact["source_key"])
            return {"status": "CREATED_AUTO_READY", "content_sha256": "a" * 64}
    monkeypatch.setattr(cli, "NotionDraftWriter", Writer)
    result = cli.run_batch(commit=True, max_minutes=100)
    assert called == [(ids[0], "51"), (ids[1], "52"), (ids[2], "52")]
    assert submitted == ["overseas-stories:51", "overseas-stories:52"]
    assert result["created"] == 2 and result["model_rejected"] == 1
    assert result["status"] == "PARTIAL_MODEL_REJECTED"
    assert result["last_issue_number"] == "52"
