"""Read-only audit of the original Gate 2 production snapshot (not a regression test)."""

import hashlib
import json
import subprocess
from pathlib import Path

from src.state import validate_state


COMMIT = "70c7a7beb2b3b84898df8191decadbc8dcb295c9"
EXPECTED_SHA256 = "04c4a2f479e785351b9b882f7c7409512a8c0310423337f501eb26dd42e2814e"
EXPECTED_AI_PENDING = 321


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    raw = subprocess.run(
        ["git", "show", f"{COMMIT}:seen_posts.json"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=20,
    ).stdout
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"historical_state_sha256_mismatch={digest}")
    state = json.loads(raw)
    validate_state(state)
    topics = state["topics"]
    ai = topics.get("ai_tech", {})
    ai_pending = len(ai.get("pending_posts", {}))
    if ai_pending != EXPECTED_AI_PENDING:
        raise SystemExit(f"historical_ai_pending_mismatch={ai_pending}")
    print(f"historical_commit={COMMIT}")
    print(f"historical_state_sha256={digest}")
    print(f"historical_ai_pending={ai_pending}")
    print("historical_state_schema=validated")
    print("scope=read_only_snapshot_integrity_only; no workflow invocation evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
