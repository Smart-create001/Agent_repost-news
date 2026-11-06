"""Fail-closed interpretation of capture-only controls for GitHub Actions."""

import argparse
import os


class GateModeError(ValueError):
    """Workflow inputs or repository variables are missing or ambiguous."""


def verification_eligible(*, run_result: str, capture_only: str,
                          capture_outcome: str, persist_outcome: str,
                          remote_verification_outcome: str,
                          processing_outcome: str) -> bool:
    """Allow fresh-runner verification only after complete capture-only success."""
    return (run_result == "success" and capture_only == "true"
            and capture_outcome == "success" and persist_outcome == "success"
            and remote_verification_outcome == "success"
            and processing_outcome == "skipped")


def _boolean(value: str | None, name: str, *, allow_missing: bool = False) -> bool | None:
    normalized = (value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    if not normalized and allow_missing:
        return None
    raise GateModeError(f"{name} must be explicitly true or false")


def resolve_capture_only(event_name: str, capture_input: str | None,
                         capture_variable: str | None) -> bool:
    """Return capture-only mode; absent controls fail closed except explicit opt-in."""
    if event_name not in {"schedule", "workflow_dispatch"}:
        raise GateModeError("Unsupported workflow event")
    manual_capture = _boolean(capture_input, "capture_only input", allow_missing=True)
    variable = _boolean(capture_variable, "PENDING_QUEUE_CAPTURE_ONLY", allow_missing=True)
    if manual_capture is True:
        return True
    if variable is None:
        raise GateModeError("PENDING_QUEUE_CAPTURE_ONLY is not configured")
    return variable


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--capture-input", default=os.environ.get("CAPTURE_ONLY_INPUT"))
    parser.add_argument("--capture-variable", default=os.environ.get("PENDING_QUEUE_CAPTURE_ONLY"))
    args = parser.parse_args()
    capture_only = resolve_capture_only(args.event_name, args.capture_input, args.capture_variable)
    print(f"capture_only={str(capture_only).lower()}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"capture_only={str(capture_only).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
