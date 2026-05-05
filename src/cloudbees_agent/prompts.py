from pathlib import Path

import yaml
from pydantic import BaseModel


class PromptLayer(BaseModel):
    """Tunable prompt layers for the repo question agent."""

    system: str
    tool_policy: str
    output_rules: str

    @property
    def instructions(self) -> str:
        """Compose prompt layers in deterministic order."""
        return "\n\n".join(
            (self.system.strip(), self.tool_policy.strip(), self.output_rules.strip())
        ) + "\n"


class PromptConfig(BaseModel):
    """Prompt configuration loaded from YAML."""

    agent: PromptLayer


def load_prompt_config(path: Path) -> PromptConfig:
    """Load prompt YAML and fail clearly when the file is absent."""
    if not path.exists():
        raise FileNotFoundError(f"Prompt config not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return PromptConfig.model_validate(payload)
