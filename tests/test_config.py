"""Tests for application configuration."""

from claude_code.config import MODEL, load_config


def test_load_config_reads_api_key_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-key")

    config = load_config()

    assert config.api_key == "environment-key"
    assert MODEL == "claude-sonnet-5-5"


def test_load_config_reads_api_key_from_dotenv_file(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.api_key == "dotenv-key"
