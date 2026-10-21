"""Structural checks for the bundled coding-agent evaluations."""

import json
import runpy
import sys
from pathlib import Path

from evals import run_evals as eval_runner
from evals.run_evals import TASKS_DIR


def test_eval_suite_has_eight_tasks_with_failing_initial_checks() -> None:
    task_directories = sorted(
        path
        for path in TASKS_DIR.iterdir()
        if path.is_dir()
        and (path / "TASK.md").is_file()
        and (path / "check.py").is_file()
    )

    assert len(task_directories) == 8
    for task_directory in task_directories:
        sys.path.insert(0, str(task_directory))
        try:
            try:
                runpy.run_path(str(task_directory / "check.py"), run_name="__main__")
            except (AssertionError, ZeroDivisionError, ValueError):
                continue
            raise AssertionError(f"{task_directory.name} passed before evaluation.")
        finally:
            sys.path.remove(str(task_directory))


def test_run_evals_saves_results_and_prints_pass_rate(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    task_directories = []
    for name in ("01_example", "02_example"):
        task_directory = tmp_path / name
        task_directory.mkdir()
        (task_directory / "TASK.md").write_text("Fix the task.", encoding="utf-8")
        (task_directory / "check.py").write_text("", encoding="utf-8")
        task_directories.append(task_directory)

    def run_task(task_directory: Path) -> dict[str, object]:
        return {
            "task": task_directory.name,
            "passed": task_directory.name == "01_example",
            "elapsed_seconds": 1.25,
            "tokens_used": 42,
            "tokens_estimated": False,
            "agent_returncode": 0,
            "check_returncode": 0,
            "error": None,
            "agent_output": "",
            "check_output": "",
        }

    results_path = tmp_path / "results.json"
    monkeypatch.setattr(eval_runner, "TASKS_DIR", tmp_path)
    monkeypatch.setattr(eval_runner, "RESULTS_PATH", results_path)
    monkeypatch.setattr(eval_runner, "run_task", run_task)

    results = eval_runner.run_evals()

    report = json.loads(results_path.read_text(encoding="utf-8"))
    assert len(results) == 2
    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["pass_rate"] == 0.5
    assert "Pass rate: 1/2 (50.0%)" in capsys.readouterr().out
