"""Fail-closed Capture-only mode and fresh-runner eligibility tests."""

import pytest

from scripts.gate2_mode import GateModeError, resolve_capture_only, verification_eligible


@pytest.mark.parametrize("event,input_value,variable", [
    ("workflow_dispatch", "true", "false"),
    ("workflow_dispatch", "false", "true"),
    ("schedule", "", "true"),
])
def test_capture_only_manual_and_scheduled_modes(event, input_value, variable):
    assert resolve_capture_only(event, input_value, variable) is True


def test_manual_processing_requires_explicit_false_variable():
    assert resolve_capture_only("workflow_dispatch", "false", "false") is False


@pytest.mark.parametrize("event,input_value,variable", [
    ("workflow_dispatch", "false", None),
    ("schedule", None, None),
    ("workflow_dispatch", "perhaps", "false"),
    ("schedule", None, "1"),
])
def test_missing_or_ambiguous_variables_fail_closed(event, input_value, variable):
    with pytest.raises(GateModeError):
        resolve_capture_only(event, input_value, variable)


def test_explicit_manual_capture_only_is_safe_without_repository_variable():
    assert resolve_capture_only("workflow_dispatch", "true", None) is True


def test_fresh_runner_requires_successful_capture_persist_verify_and_skipped_processing():
    evidence = dict(run_result="success", capture_only="true", capture_outcome="success",
                    persist_outcome="success", remote_verification_outcome="success",
                    processing_outcome="skipped")
    assert verification_eligible(**evidence)
    for key, value in [
        ("run_result", "failure"), ("capture_only", "false"),
        ("capture_outcome", "failure"), ("persist_outcome", "failure"),
        ("remote_verification_outcome", "failure"), ("processing_outcome", "success"),
        ("capture_outcome", "skipped"), ("persist_outcome", "skipped"),
        ("remote_verification_outcome", "skipped"), ("processing_outcome", ""),
    ]:
        assert not verification_eligible(**{**evidence, key: value})


def test_workflow_conditions_use_resolved_fail_closed_gate():
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[1]
    config = yaml.load((root / ".github/workflows/agent.yml").read_text(), Loader=yaml.BaseLoader)
    jobs = config["jobs"]
    steps = jobs["run"]["steps"]
    resolve = next(step for step in steps if step.get("id") == "resolve_mode")
    assert "scripts.gate2_mode" in resolve["run"]
    agent = next(step for step in steps if step.get("id") == "agent")
    assert "steps.resolve_mode.outputs.capture_only == 'false'" in agent["if"]
    verify_job = jobs["verify-capture-only"]
    for output, expected in [
        ("capture_only", "'true'"), ("capture_outcome", "'success'"),
        ("persist_capture_outcome", "'success'"),
        ("remote_verification_outcome", "'success'"),
        ("processing_outcome", "'skipped'"),
    ]:
        assert f"needs.run.outputs.{output} == {expected}" in verify_job["if"]
