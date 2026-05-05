from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from cloudbees_agent.repo import normalize_repo


class ToolName(StrEnum):
    """Evidence sources the agent is allowed to inspect."""

    README = "readme"
    ISSUES = "issues"
    COMMITS = "commits"
    CODE_SEARCH = "code_search"


class EvidenceItem(BaseModel):
    """One source snippet used to ground an answer."""

    kind: str
    title: str
    url: str | None = None
    path: str | None = None
    excerpt: str


class EvidenceResult(BaseModel):
    """All evidence returned from a single tool call."""

    tool: ToolName
    query: str
    summary: str
    items: list[EvidenceItem]


class FinalAnswer(BaseModel):
    """CLI-facing answer plus trace and tool metadata."""

    answer: str
    evidence_refs: list[str] = Field(default_factory=list)
    tool_calls: list[ToolName] = Field(default_factory=list)
    fallback_used: bool = False
    fallback_tool: ToolName | None = None
    trace_path: Path
    session_id: str

    model_config = {"arbitrary_types_allowed": True}


class LocalTrace(BaseModel):
    """Durable local JSON record of one agent turn."""

    repo: str
    question: str
    session_id: str
    tool_calls: list[ToolName] = Field(default_factory=list)
    evidence_summary: str
    final_answer: str
    evidence: list[EvidenceResult]
    fallback_tool: ToolName | None = None
    fallback_reason: str | None = None
    conversation_turn: int = 1


class EvalCase(BaseModel):
    """One fixture eval scenario and expected tool behavior."""

    name: str
    repo: str
    question: str
    mocked_outputs: dict[ToolName, EvidenceResult]
    expected_first_tool: ToolName
    expected_fallback: ToolName | None = None
    required_evidence_fields: list[str] = Field(default_factory=list)

    @field_validator("repo")
    @classmethod
    def normalize_repository(cls, value: str) -> str:
        """Normalize eval repos so fixtures match runtime behavior."""
        return normalize_repo(value)


class EvalSummary(BaseModel):
    """Aggregate agent scores printed by the eval command."""

    total: int
    first_tool_accuracy: float
    fallback_success_rate: float
    grounded_answer_rate: float
    trace_completeness: float
