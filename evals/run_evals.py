"""Run the coding-agent evaluation tasks in isolated temporary directories."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVALS_DIR.parent
TASKS_DIR = EVALS_DIR / "tasks"
RESULTS_PATH = EVALS_DIR / "results.json"
TOKEN_PATTERN = re.compile(r"EVAL_TOKENS_USED=(\d+)")
ESTIMATE_PATTERN = re.compile(r"EVAL_TOKENS_ESTIMATED=(true|false)")


def run_task(task_directory: Path, timeout_seconds: int = 300) -> dict[str, Any]:
    """Run the agent and task check in a temporary copy of one task."""
    started = time.perf_counter()
    task_name = task_directory.name
    task_prompt = (task_directory / "TASK.md").read_text(encoding="utf-8")
    agent_result: subprocess.CompletedProcess[str] | None = None
    check_result: subprocess.CompletedProcess[str] | None = None
    error: str | None = None
    tokens_used = 0
    tokens_estimated = True

    with tempfile.TemporaryDirectory(prefix=f"claude-code-eval-{task_name}-") as temp:
        workspace = Path(temp)
        shutil.copytree(task_directory, workspace, dirs_exist_ok=True)
        environment = os.environ.copy()
        source_path = str(PROJECT_ROOT / "src")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_path
            if not existing_pythonpath
            else source_path + os.pathsep + existing_pythonpath
        )

        try:
            agent_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "claude_code.cli",
                    "--prompt",
                    task_prompt,
                    "--yolo",
                ],
                cwd=workspace,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            token_match = TOKEN_PATTERN.search(agent_result.stdout)
            estimate_match = ESTIMATE_PATTERN.search(agent_result.stdout)
            if token_match:
                tokens_used = int(token_match.group(1))
            if estimate_match:
                tokens_estimated = estimate_match.group(1) == "true"
        except subprocess.TimeoutExpired:
            error = f"Agent timed out after {timeout_seconds} seconds."
        except OSError as exception:
            error = f"Unable to start agent: {exception}"

        try:
            check_result = subprocess.run(
                [sys.executable, "check.py"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exception:
            error = f"Unable to run check script: {exception}"

    elapsed_seconds = round(time.perf_counter() - started, 3)
    passed = check_result is not None and check_result.returncode == 0
    return {
        "task": task_name,
        "passed": passed,
        "elapsed_seconds": elapsed_seconds,
        "tokens_used": tokens_used,
        "tokens_estimated": tokens_estimated,
        "agent_returncode": (
            agent_result.returncode if agent_result is not None else None
        ),
        "check_returncode": (
            check_result.returncode if check_result is not None else None
        ),
        "error": error,
        "agent_output": (
            agent_result.stdout + agent_result.stderr
            if agent_result is not None
            else ""
        ),
        "check_output": (
            check_result.stdout + check_result.stderr
            if check_result is not None
            else ""
        ),
    }


def run_evals() -> list[dict[str, Any]]:
    """Run all task folders, persist the results, and print a summary table."""
    task_directories = sorted(
        path
        for path in TASKS_DIR.iterdir()
        if path.is_dir()
        and (path / "TASK.md").is_file()
        and (path / "check.py").is_file()
    )
    if not task_directories:
        raise RuntimeError(f"No valid evaluation tasks found in {TASKS_DIR}.")

    results = [run_task(task_directory) for task_directory in task_directories]
    passed_count = sum(result["passed"] for result in results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed_count,
        "total": len(results),
        "pass_rate": passed_count / len(results),
        "results": results,
    }
    RESULTS_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{'Task':<24} {'Result':<8} {'Time (s)':>9} {'Tokens':>10}")
    print("-" * 55)
    for result in results:
        token_display = str(result["tokens_used"])
        if result["tokens_estimated"]:
            token_display += "~"
        print(
            f"{result['task']:<24} "
            f"{'PASS' if result['passed'] else 'FAIL':<8} "
            f"{result['elapsed_seconds']:>9.3f} "
            f"{token_display:>10}"
        )
    print("-" * 55)
    print(f"Pass rate: {passed_count}/{len(results)} ({report['pass_rate']:.1%})")
    print(f"Results saved to {RESULTS_PATH}")
    return results


if __name__ == "__main__":
    run_evals()
