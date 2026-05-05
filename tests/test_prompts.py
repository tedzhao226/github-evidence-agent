from pathlib import Path

import pytest

from cloudbees_agent.prompts import load_prompt_config


def test_prompt_config_composes_agent_layers(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(
        """
agent:
  system: |
    System layer.
  tool_policy: |
    Tool layer.
  output_rules: |
    Output layer.
""",
        encoding="utf-8",
    )

    config = load_prompt_config(path)

    assert config.agent.instructions == "System layer.\n\nTool layer.\n\nOutput layer.\n"


def test_missing_prompt_config_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="Prompt config not found"):
        load_prompt_config(tmp_path / "missing.yaml")


def test_default_prompt_config_exists():
    config = load_prompt_config(Path("prompts/agent.yaml"))

    assert "Available tools" in config.agent.instructions
    assert "multiple tools" in config.agent.instructions
