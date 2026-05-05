from collections.abc import Callable
import os
from pathlib import Path
from typing import Any, Protocol

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.test import TestModel

from cloudbees_agent.models import EvidenceResult, FinalAnswer, ToolName
from cloudbees_agent.prompts import load_prompt_config
from cloudbees_agent.repo import normalize_repo
from cloudbees_agent.traceability import (
    configure_logfire,
    evidence_refs,
    judge_fallback,
    new_session_id,
    trace_path,
    trace_span,
    write_local_trace,
)


class EvidenceTools(Protocol):
    """Tool adapter contract used by the agent session."""

    def run(self, tool: ToolName, repo: str, question: str) -> EvidenceResult:
        """Return evidence for one selected source."""
        ...


def default_model() -> Any:
    """Use OpenAI when configured and a local test model for offline runs."""
    if os.getenv("OPENAI_API_KEY"):
        return os.getenv("OPENAI_MODEL", "openai:gpt-5-mini")
    return TestModel(custom_output_text="No OpenAI key is configured, so this is a local tool check.")


def summarize_tool_result(result: EvidenceResult) -> str:
    """Return compact text for the model after a tool call."""
    if not result.items:
        return f"{result.tool.value} found no relevant evidence."
    lines = [f"{result.tool.value} found {len(result.items)} item(s):"]
    for item in result.items[:5]:
        ref = item.url or item.path or item.title
        lines.append(f"- {item.title}: {item.excerpt} ({ref})")
    return "\n".join(lines)


class AgentSession:
    """One conversational CLI session for a single GitHub repository."""

    def __init__(
        self,
        repo: str,
        tools: EvidenceTools,
        trace_dir: Path = Path("traces"),
        model: Any | Callable[[], Any] | None = None,
        prompt_config: Path | None = None,
        session_id: str | None = None,
    ) -> None:
        """Store session context, tool backend, and prompt/model settings."""
        self.repo = normalize_repo(repo)
        self.tools = tools
        self.trace_dir = trace_dir
        self.model = model
        self.prompt_config = prompt_config or Path(os.getenv("PROMPT_CONFIG", "prompts/agent.yaml"))
        self.session_id = session_id or new_session_id()
        self.message_history: list[ModelMessage] = []
        self.turn = 0

    def ask(self, question: str) -> FinalAnswer:
        """Run one conversation turn and keep model history for follow-up turns."""
        configure_logfire()
        self.turn += 1
        evidence: list[EvidenceResult] = []

        with trace_span("agent.turn", repo=self.repo, turn=self.turn):
            agent = self._build_agent(evidence)
            try:
                result = agent.run_sync(
                    f"Repository: {self.repo}\nQuestion: {question}",
                    message_history=self.message_history,
                    usage_limits=UsageLimits(request_limit=6, tool_calls_limit=8),
                )
                output = str(result.output)
                self.message_history = result.all_messages()
            except UsageLimitExceeded:
                output = build_limited_answer(question, evidence)
            path = trace_path(self.trace_dir, self.session_id, self.repo, self.turn)
            fallback_used, fallback_tool, fallback_reason = judge_fallback(evidence)
            answer = FinalAnswer(
                answer=output,
                evidence_refs=evidence_refs(evidence),
                tool_calls=[item.tool for item in evidence],
                fallback_used=fallback_used,
                fallback_tool=fallback_tool,
                trace_path=path,
                session_id=self.session_id,
            )
            write_local_trace(
                path=path,
                repo=self.repo,
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

    def clear(self) -> None:
        """Reset conversation memory while keeping the same repository and tools."""
        self.message_history = []
        self.turn = 0

    def _build_agent(self, evidence: list[EvidenceResult]) -> Agent:
        """Create a Pydantic AI agent and bind GitHub tools for one turn."""
        model = self.model() if callable(self.model) else self.model
        prompt = load_prompt_config(self.prompt_config).agent.instructions
        agent = Agent(
            model or default_model(),
            output_type=str,
            instructions=prompt,
            tool_timeout=60,
        )

        @agent.tool_plain(name="readme")
        def readme(query: str = "") -> str:
            """Read repository README evidence for the current question."""
            return self._run_tool(ToolName.README, query, evidence)

        @agent.tool_plain(name="issues")
        def issues(query: str = "") -> str:
            """Search public repository issues for the current question."""
            return self._run_tool(ToolName.ISSUES, query, evidence)

        @agent.tool_plain(name="commits")
        def commits(query: str = "") -> str:
            """Read recent commit evidence for the current question."""
            return self._run_tool(ToolName.COMMITS, query, evidence)

        @agent.tool_plain(name="code_search")
        def code_search(query: str = "") -> str:
            """Search a shallow local clone for implementation evidence."""
            return self._run_tool(ToolName.CODE_SEARCH, query, evidence)

        return agent

    def _run_tool(
        self,
        tool: ToolName,
        query: str,
        evidence: list[EvidenceResult],
    ) -> str:
        """Call one GitHub evidence tool, record it, and return model-facing text."""
        with trace_span("tool.run", repo=self.repo, tool=tool.value):
            result = self.tools.run(tool, self.repo, query)
            evidence.append(result)
            return summarize_tool_result(result)


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
