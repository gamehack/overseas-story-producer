import pytest

from overseas_story_producer import cli
from overseas_story_producer.core import ProductionError


@pytest.mark.parametrize("status", ["COMPLETED", "SKIPPED"])
def test_batch_result_accepts_only_fully_processed_or_empty_batches(status):
    assert cli.validate_batch_result({"status": status}) == status


@pytest.mark.parametrize("status", [
    "PARTIAL_MODEL_REJECTED",
    "PARTIAL_TIME_BUDGET",
    "ERROR",
    "PLANNED",
    "UNKNOWN",
    None,
])
def test_batch_result_rejects_partial_unknown_and_nonterminal_statuses(status):
    with pytest.raises(ProductionError, match="did not complete"):
        cli.validate_batch_result({"status": status})


@pytest.mark.parametrize("value", [None, [], "COMPLETED", {"other": "value"}])
def test_batch_result_rejects_malformed_payload(value):
    with pytest.raises(ProductionError, match="invalid batch result"):
        cli.validate_batch_result(value)
