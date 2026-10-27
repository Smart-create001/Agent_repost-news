"""Phase 6 workflow and local Git persistence checks."""

from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/agent.yml"
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
