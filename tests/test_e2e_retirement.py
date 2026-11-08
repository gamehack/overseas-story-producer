from pathlib import Path


def test_completed_integration_e2e_cannot_recreate_deleted_test_area_on_push():
    workflow = (Path(__file__).resolve().parents[1]
                / ".github" / "workflows" / "automatic-e2e.yml").read_text()
    assert "vars.M7_E2E_ENABLED == 'true' && vars.M7_E2E_ACTIVE == 'true'" in workflow
