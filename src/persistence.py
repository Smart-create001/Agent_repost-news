"""Verify that a captured state snapshot is visible on the remote Git branch."""

import hashlib
from pathlib import Path
import subprocess

from src.state import StateError, load_state


def verify_remote_state(state_path: str | Path, commit: str, *, repo: str | Path = ".") -> None:
    """Fail closed unless remote main still points at the exact captured state."""
    path = Path(state_path)
    if path.name != "seen_posts.json" or not commit or len(commit) < 12:
        raise StateError("Durable state verification requires a commit and seen_posts.json")
    load_state(path)
    try:
        subprocess.run(["git", "fetch", "--no-tags", "origin", "main"], cwd=repo,
                       check=True, capture_output=True, timeout=30)
        remote = subprocess.run(["git", "rev-parse", "refs/remotes/origin/main"], cwd=repo,
                                check=True, capture_output=True, timeout=10).stdout.decode().strip()
        if remote != commit:
            raise StateError("Remote main moved after pending state persistence")
        saved = subprocess.run(["git", "show", f"{commit}:seen_posts.json"], cwd=repo,
                               check=True, capture_output=True, timeout=10).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise StateError("Cannot verify remote pending state") from exc
    if hashlib.sha256(saved).digest() != hashlib.sha256(path.read_bytes()).digest():
        raise StateError("Remote pending state differs from local state")


def persist_remote_state(state_path: str | Path, *, repo: str | Path = ".") -> str:
    """Commit and push a state transition, then read it back from remote main."""
    try:
        script = Path(__file__).resolve().parents[1] / "scripts/persist_state.sh"
        subprocess.run(["bash", str(script)], cwd=repo, check=True,
                       capture_output=True, timeout=60)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                                capture_output=True, timeout=10).stdout.decode().strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise StateError("State push failed; local state requires reconciliation") from exc
    verify_remote_state(state_path, commit, repo=repo)
    return commit
