"""Append complete CLI conversation transcripts to the local session log."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def append_session_log(
    transcript: list[dict[str, Any]],
    project_root: Path,
    started_at: datetime,
    log_directory: Path | None = None,
) -> Path:
    """Append one JSON-lines record for a completed CLI session."""
    destination = log_directory or Path.home() / ".shree" / "logs"
    destination.mkdir(parents=True, exist_ok=True)
    log_path = destination / "sessions.jsonl"
    record = {
        "session_id": str(uuid4()),
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root.resolve()),
        "transcript": transcript,
    }
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return log_path
