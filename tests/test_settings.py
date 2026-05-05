from pathlib import Path

from cloudbees_agent.settings import AppSettings, load_settings


def test_load_settings_reads_env_file_without_overriding_shell_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=from-file\nOPENAI_MODEL=openai:gpt-5-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    settings = load_settings(env_path)

    assert settings.openai_api_key == "from-shell"
    assert settings.openai_model == "openai:gpt-5-mini"


def test_app_settings_parses_booleans_paths_and_blank_tokens(tmp_path):
    settings = AppSettings(
        _env_file=None,
        logfire_console="true",
        github_token="",
        clone_root=str(tmp_path / "repos"),
        sandbox_root=str(tmp_path / "sandbox"),
        prompt_config="prompts/custom.yaml",
    )

    assert settings.logfire_console is True
    assert settings.github_token is None
    assert settings.clone_root == tmp_path / "repos"
    assert settings.sandbox_root == tmp_path / "sandbox"
    assert settings.prompt_config == Path("prompts/custom.yaml")


def test_app_settings_invalid_trace_backend_uses_default():
    settings = AppSettings(_env_file=None, trace_backend="bad")

    assert settings.trace_backend == "both"
