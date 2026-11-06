"""Phase 6 workflow and local Git persistence checks."""

from pathlib import Path
import os
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/agent.yml"
PROBE = ROOT / ".github/workflows/phase7-probe.yml"
SCRIPT = ROOT / "scripts/persist_state.sh"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def setup_git(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "-b", "main", str(work))
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "test@example.org")
    (work / "seen_posts.json").write_text('{"schema_version":1,"topics":{}}\n')
    git(work, "add", "seen_posts.json")
    git(work, "commit", "-m", "init")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-u", "origin", "main")
    return work, remote


def workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def test_tc6_01_02_03_workflow_and_triggers():
    config = workflow()
    assert "workflow_dispatch" in config["on"]
    assert len(config["on"]["schedule"]) == 2
    assert "briefing" in WORKFLOW.read_text()


def test_tc6_04_05_06_secrets_permissions_concurrency():
    config = workflow()
    assert config["jobs"]["run"]["if"] == "vars.AGENT_ENABLED == 'true'"
    assert config["permissions"] == {"contents": "write"}
    assert config["concurrency"]["cancel-in-progress"] == "false"
    assert "github.repository" in config["concurrency"]["group"]
    text = WORKFLOW.read_text()
    for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert "${{ secrets." + name + " }}" in text
    assert "force" not in SCRIPT.read_text().lower()


def test_tc6_07_persist_and_reload(tmp_path):
    work, remote = setup_git(tmp_path)
    (work / "seen_posts.json").write_text('{"schema_version":1,"topics":{"a":{"posts":{},"briefings":{}}}}\n')
    subprocess.run(["bash", str(SCRIPT)], cwd=work, check=True, capture_output=True)
    assert git(work, "rev-parse", "HEAD") == git(work, "rev-parse", "refs/remotes/origin/main")
    clone = tmp_path / "next-run"
    git(tmp_path, "clone", "-b", "main", str(remote), str(clone))
    assert '"a"' in (clone / "seen_posts.json").read_text()


def test_tc6_08_conflict_preserves_newer_remote(tmp_path):
    work, remote = setup_git(tmp_path)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-b", "main", str(remote), str(other))
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.org")
    (other / "seen_posts.json").write_text('{"schema_version":1,"topics":{"remote":{"posts":{},"briefings":{}}}}\n')
    git(other, "add", "seen_posts.json")
    git(other, "commit", "-m", "other update")
    git(other, "push", "origin", "main")
    (work / "seen_posts.json").write_text('{"schema_version":1,"topics":{"local":{"posts":{},"briefings":{}}}}\n')
    result = subprocess.run(["bash", str(SCRIPT)], cwd=work, capture_output=True, text=True)
    assert result.returncode == 3
    assert '"local"' in (work / "seen_posts.json").read_text()
    inspect = tmp_path / "inspect"
    git(tmp_path, "clone", "-b", "main", str(remote), str(inspect))
    assert '"remote"' in (inspect / "seen_posts.json").read_text()


def test_tc6_09_failure_propagation_and_shell_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    text = WORKFLOW.read_text()
    assert "if: failure()" in text
    assert 'exit 1' in text


def test_tc6_10_live_protection_not_verified():
    # Local tests cannot prove branch protection or token write permission.
    assert "git push origin HEAD:main" in SCRIPT.read_text()


def test_phase7_probe_is_manual_read_only_and_has_no_telegram_secret():
    text = PROBE.read_text()
    config = yaml.load(text, Loader=yaml.BaseLoader)
    assert list(config["on"]) == ["workflow_dispatch"]
    assert config["permissions"] == {"contents": "read"}
    assert "TELEGRAM_BOT_TOKEN" not in text and "TELEGRAM_CHAT_ID" not in text
    assert "persist_state" not in text and "seen_posts.json" not in text


def test_rejected_push_preserves_local_and_remote_state(tmp_path):
    work, remote = setup_git(tmp_path)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (work / "seen_posts.json").write_text('{"schema_version":1,"topics":{"local":{"posts":{},"briefings":{}}}}\n')
    result = subprocess.run(["bash", str(SCRIPT)], cwd=work, capture_output=True, text=True)
    assert result.returncode == 4
    assert '"local"' in (work / "seen_posts.json").read_text()
    check = tmp_path / "remote-check"
    git(tmp_path, "clone", "-b", "main", str(remote), str(check))
    assert '"local"' not in (check / "seen_posts.json").read_text()


def test_pending_capture_is_pushed_before_normal_alert_processing():
    config = workflow()
    steps = config["jobs"]["run"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Capture alert candidates before processing") < names.index("Persist captured candidates")
    assert names.index("Persist captured candidates") < names.index("Run agent")
    capture = next(step for step in steps if step.get("id") == "capture")
    assert "--capture-pending" in capture["run"]
    assert "inputs.phase6_test != true" in capture["if"]
    persist = next(step for step in steps if step.get("id") == "persist_capture")
    assert "steps.capture.outcome == 'success'" in persist["if"]
    assert "scripts/persist_state.sh" in persist["run"]
    verify = next(step for step in steps if step.get("id") == "verify_capture")
    assert "steps.persist_capture.outcome == 'success'" in verify["if"]
    assert "verify_remote_state" in verify["run"]
    partial = next(step for step in steps if step.get("id") == "persist_partial")
    assert "failure()" in partial["if"] and "steps.capture.outcome == 'failure'" in partial["if"]
    assert "load_state" in partial["run"] and "scripts/persist_state.sh" in partial["run"]
    agent = next(step for step in steps if step.get("id") == "agent")
    assert "steps.verify_capture.outcome == 'success'" in agent["if"]
    assert "steps.capture.outcome == 'skipped'" in agent["if"]
    assert "--process-pending --persisted-commit" in agent["run"]
    assert "steps.persist_capture.outputs.commit" in agent["env"]["PERSISTED_COMMIT"]


def test_capture_shell_propagates_exit_code_instead_of_reporting_success(tmp_path):
    capture = next(step for step in workflow()["jobs"]["run"]["steps"] if step.get("id") == "capture")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text('#!/bin/sh\nexit "$FAKE_CAPTURE_EXIT"\n')
    fake_python.chmod(0o755)
    for code in (0, 1, 2):
        output = tmp_path / f"output-{code}"
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
               "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(output),
               "FAKE_CAPTURE_EXIT": str(code)}
        result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", capture["run"]],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        assert result.returncode == code
        assert f"exit_code={code}" in output.read_text()


def test_capture_only_mode_persists_then_verifies_on_fresh_read_only_job():
    config = workflow()
    jobs = config["jobs"]
    steps = jobs["run"]["steps"]
    assert "capture_only" in config["on"]["workflow_dispatch"]["inputs"]
    capture = next(step for step in steps if step.get("id") == "capture")
    assert "steps.resolve_mode.outputs.capture_only == 'true'" in capture["if"]
    assert all(secret not in capture.get("env", {}) for secret in
               ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))
    agent = next(step for step in steps if step.get("id") == "agent")
    assert "steps.resolve_mode.outputs.capture_only == 'false'" in agent["if"]
    verify = jobs["verify-capture-only"]
    assert verify["permissions"] == {"contents": "read"}
    for output, expected in [("capture_only", "'true'"), ("capture_outcome", "'success'"),
                             ("persist_capture_outcome", "'success'"),
                             ("remote_verification_outcome", "'success'"),
                             ("processing_outcome", "'skipped'")]:
        assert f"needs.run.outputs.{output} == {expected}" in verify["if"]
    assert all("TELEGRAM" not in step.get("env", {}) for step in verify["steps"])
    readback = verify["steps"][-1]["run"]
    assert "verify_pending_snapshot" in readback
    assert "--expected-commit" in readback and "--expected-sha256" in readback
    assert "--processing-outcome" in readback
    outputs = jobs["run"]["outputs"]
    assert "steps.verify_capture.outputs.persisted_commit" in outputs["persisted_commit"]
    assert "steps.verify_capture.outputs.state_sha256" in outputs["state_sha256"]
    assert "steps.agent.outcome" in outputs["processing_outcome"]
    assert "steps.resolve_mode.outputs.capture_only" in outputs["capture_only"]
    assert "needs.run.outputs.processing_outcome" in verify["steps"][-1].get("env", {}).get("PROCESSING_OUTCOME", "")
    verifier = (ROOT / "scripts/verify_pending_snapshot.py").read_text()
    assert '"show", f"{expected_commit}:seen_posts.json"' in verifier
    assert "SHA-256 mismatch" in verifier and "validate_state(state)" in verifier
