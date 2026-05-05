from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from cloudbees_agent.models import EvidenceResult, FinalAnswer, ToolName
from cloudbees_agent.prompts import load_prompt_config
from cloudbees_agent.repo import normalize_repo
from cloudbees_agent.settings import AppSettings, load_settings
from cloudbees_agent.tools import split_evidence_refs, summarize_tool_result
from cloudbees_agent.traceability import (
    configure_logfire,
    judge_fallback,
    new_session_id,
    record_turn_trace,
    trace_path,
    trace_span,
)


class EvidenceTools(Protocol):
    """Tool adapter contract used by the agent session."""

    def readme(self, repo: str, query: str) -> EvidenceResult:
        """Return README evidence."""
        ...

    def issues(self, repo: str, query: str) -> EvidenceResult:
        """Return issue evidence."""
        ...

    def commits(self, repo: str, query: str) -> EvidenceResult:
        """Return commit evidence."""
        ...

    def code_search(self, repo: str, query: str) -> EvidenceResult:
        """Return code-search evidence."""
        ...


ModelSpec = Any | Callable[[], Any]
EvidenceLookup = Callable[[str, str], EvidenceResult]


def default_model(settings: AppSettings) -> Any:
    """Use OpenAI when configured and a local test model for offline runs."""
    if settings.openai_api_key:
        model_name = settings.openai_model.removeprefix("openai:")
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(api_key=settings.openai_api_key),
        )
    return TestModel(custom_output_text="No OpenAI key is configured, so this is a local tool check.")


@dataclass
class ToolCallRecorder:
    """Per-turn evidence holder for closure-based tools."""

    repo: str
    evidence: list[EvidenceResult] = field(default_factory=list)


class AgentSession:
    """One conversational CLI session."""

    def __init__(
        self,
        tools: EvidenceTools,
        settings: AppSettings | None = None,
        trace_dir: Path = Path("traces"),
        model: ModelSpec | None = None,
        prompt_config: Path | None = None,
        session_id: str | None = None,
    ) -> None:
        """Store session context, tool backend, and prompt/model settings."""
        self.tools = tools
        self.settings = settings or load_settings()
        self.trace_dir = trace_dir
        self.model = model
        self.prompt_config = prompt_config or self.settings.prompt_config
        self.session_id = session_id or new_session_id()
        self.message_history: list[ModelMessage] = []
        self.turn = 0
        self._backend_for: dict[ToolName, EvidenceLookup] = {
            ToolName.README: self.tools.readme,
            ToolName.ISSUES: self.tools.issues,
            ToolName.COMMITS: self.tools.commits,
            ToolName.CODE_SEARCH: self.tools.code_search,
        }

    def ask(self, repo: str, question: str) -> FinalAnswer:
        """Run one conversation turn and keep model history for follow-up turns.

        Turn lifecycle:
          ask
            ├─ _run_model_turn  → pydantic-ai runs; may call tool closures
            │     └─ tool closure → _call_tool → backend → recorder.evidence.append
            └─ _finalize_turn   → build FinalAnswer + record trace
        """
        repo = normalize_repo(repo)
        configure_logfire(self.settings)
        self.turn += 1
        recorder = ToolCallRecorder(repo=repo)

        with trace_span("agent.turn", repo=repo, turn=self.turn):
            output = self._run_model_turn(repo, question, recorder)
            return self._finalize_turn(repo, question, output, recorder.evidence)

    def clear(self) -> None:
        """Reset conversation memory while keeping the same tools and session id."""
        self.message_history = []
        self.turn = 0

    def _call_tool(self, tool: ToolName, query: str, recorder: ToolCallRecorder) -> str:
        """Look up the backend, run it, record the result, return text for the model."""
        print(f"Running tool: {tool.value}")
        query = query.strip()
        with trace_span("tool.run", repo=recorder.repo, tool=tool.value):
            if query:
                result = self._backend_for[tool](recorder.repo, query)
            else:
                result = EvidenceResult(tool=tool, query="", summary="", items=[])
            recorder.evidence.append(result)
            return summarize_tool_result(result)

    def _run_model_turn(self, repo: str, question: str, recorder: ToolCallRecorder) -> str:
        """Run the model once and update session history on success."""
        agent = self._build_pydantic_agent(repo, recorder)
        try:
            result = agent.run_sync(
                f"Repository: {repo}\nQuestion: {question}",
                message_history=self.message_history,
                usage_limits=UsageLimits(request_limit=6, tool_calls_limit=8),
            )
            self.message_history = result.all_messages()
            return str(result.output)
        except UsageLimitExceeded:
            return build_limited_answer(question, recorder.evidence)

    def _finalize_turn(
        self,
        repo: str,
        question: str,
        output: str,
        evidence: list[EvidenceResult],
    ) -> FinalAnswer:
        """Convert tool evidence into a user answer and trace record."""
        path = trace_path(self.trace_dir, self.session_id, repo, self.turn)
        fallback_used, fallback_tool, fallback_reason = judge_fallback(evidence)
        evidence_refs, code_refs = split_evidence_refs(evidence)
        answer = FinalAnswer(
            answer=output,
            evidence_refs=evidence_refs,
            code_refs=code_refs,
            tool_calls=[item.tool for item in evidence],
            fallback_used=fallback_used,
            fallback_tool=fallback_tool,
            trace_path=path,
            session_id=self.session_id,
        )
        record_turn_trace(
            settings=self.settings,
            path=path,
            repo=repo,
            question=question,
            session_id=self.session_id,
            tool_calls=answer.tool_calls,
            evidence=evidence,
            final_answer=answer.answer,
            fallback_tool=fallback_tool,
            fallback_reason=fallback_reason,
            conversation_turn=self.turn,
        )
        return answer

    def _build_pydantic_agent(self, repo: str, recorder: ToolCallRecorder) -> Agent:
        """Create a Pydantic AI agent and bind GitHub tools for one turn."""
        model = self.model() if callable(self.model) else self.model
        prompt = load_prompt_config(self.prompt_config).agent.instructions
        return Agent(
            model or default_model(self.settings),
            output_type=str,
            instructions=prompt,
            tools=self._tool_functions(recorder),
            tool_timeout=60,
        )

    def _tool_functions(self, recorder: ToolCallRecorder) -> tuple[Callable[[str], str], ...]:
        """Create simple model tools that close over this turn's recorder."""

        def readme(query: str) -> str:
            """Read repository README evidence using this tool-specific query."""
            return self._call_tool(ToolName.README, query, recorder)

        def issues(query: str) -> str:
            """Search public repository issues using this tool-specific query."""
            return self._call_tool(ToolName.ISSUES, query, recorder)

        def commits(query: str) -> str:
            """Read recent commit evidence using this tool-specific query."""
            return self._call_tool(ToolName.COMMITS, query, recorder)

        def code_search(query: str) -> str:
            """Search a shallow local clone using this tool-specific query."""
            return self._call_tool(ToolName.CODE_SEARCH, query, recorder)

        return readme, issues, commits, code_search


def build_limited_answer(question: str, evidence: list[EvidenceResult]) -> str:
    """Build a bounded answer when the model exceeds the tool-call budget."""
    if not evidence:
        return f"I could not collect evidence before the tool-call limit was reached: {question}"
    parts = []
    for result in evidence:
        for item in result.items[:2]:
            ref = item.url or item.path or item.title
            parts.append(f"{result.tool.value} {item.title}: {item.excerpt} ({ref})")
            if len(parts) >= 5:
                break
        if len(parts) >= 5:
            break
    if not parts:
        return f"The agent reached the tool-call limit and the collected tools did not return relevant evidence for: {question}"
    return "The agent reached the tool-call limit, so this answer is based on collected evidence: " + " ".join(parts)
