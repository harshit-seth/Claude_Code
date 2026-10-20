"""Smoke test for the command-line entry point."""

from claude_code.cli import main


def test_main_prints_scaffold_message(capsys) -> None:
    main()
    assert capsys.readouterr().out == "claude-code: scaffold OK\n"
