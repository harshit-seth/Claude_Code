"""Tests for persistent CLI transcript logging."""

import json
from datetime import datetime, timezone
from pathlib import Path

from claude_code.session_log import append_session_log


def test_append_session_log_writes_full_transcript_as_json_lines(
    tmp_path: Path,
) -> None:
    transcript = [
        {"role": "user", "content": "Fix the bug"},
        {"role": "assistant", "content": "I fixed it."},
    ]

    log_path = append_session_log(
        transcript,
        tmp_path,
        datetime(2026, 10, 21, tzinfo=timezone.utc),
        log_directory=tmp_path / "logs",
    )

    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["transcript"] == transcript
    assert records[0]["project_root"] == str(tmp_path.resolve())
    assert records[0]["started_at"] == "2026-10-21T00:00:00+00:00"
    assert records[0]["session_id"]
