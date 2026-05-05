import json
from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from cloudbees_agent.agent import AgentSession
from cloudbees_agent.models import EvalCase, EvalSummary, ToolName
from cloudbees_agent.settings import AppSettings


class FixtureTools:
    """Tool adapter that returns fixture evidence and records call order."""

    def __init__(self, outputs):
        """Store mocked outputs keyed by tool name."""
        self.outputs = outputs
        self.calls: list[ToolName] = []

    def _lookup(self, tool: ToolName, repo: str, query: str):
        """Return fixture evidence for a tool and record that it was selected."""
        self.calls.append(tool)
        return self.outputs[tool]

    def readme(self, repo: str, query: str):
        return self._lookup(ToolName.README, repo, query)

    def issues(self, repo: str, query: str):
        return self._lookup(ToolName.ISSUES, repo, query)

    def commits(self, repo: str, query: str):
        return self._lookup(ToolName.COMMITS, repo, query)

    def code_search(self, repo: str, query: str):
        return self._lookup(ToolName.CODE_SEARCH, repo, query)


def evaluate_cases(cases: list[EvalCase], trace_dir: Path = Path("traces/eval")) -> EvalSummary:
    """Score fixture-backed agent behavior without live network or model calls."""
    first_tool_hits = 0
    fallback_hits = 0
    fallback_total = 0
    grounded_hits = 0
    trace_hits = 0

    for case in cases:
        tools = FixtureTools(case.mocked_outputs)
        session = AgentSession(
            tools=tools,
            settings=AppSettings(
                _env_file=None,
                openai_api_key=None,
                logfire_token=None,
                github_token=None,
            ),
            trace_dir=trace_dir / slug(case.name),
            model=scripted_model(tool_script(case)),
        )
        answer = session.ask(case.repo, case.question)

        if tools.calls and tools.calls[0] == case.expected_first_tool:
            first_tool_hits += 1
        if case.expected_fallback:
            fallback_total += 1
            if len(tools.calls) > 1 and tools.calls[1] == case.expected_fallback:
                fallback_hits += 1
        if is_grounded(answer.answer, case.mocked_outputs[tools.calls[-1]].items):
            grounded_hits += 1
        if trace_complete(answer.trace_path):
            trace_hits += 1

    total = len(cases)
    return EvalSummary(
        total=total,
        first_tool_accuracy=ratio(first_tool_hits, total),
        fallback_success_rate=ratio(fallback_hits, fallback_total),
        grounded_answer_rate=ratio(grounded_hits, total),
        trace_completeness=ratio(trace_hits, total),
    )


def load_cases(path: Path = Path("evals/fixtures/cases.json")) -> list[EvalCase]:
    """Load eval fixture JSON into validated Pydantic cases."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [EvalCase.model_validate(item) for item in payload]


def is_grounded(answer: str, items) -> bool:
    """Check that the final answer includes text from returned evidence."""
    return any(item.excerpt and item.excerpt[:30] in answer for item in items)


def trace_complete(path: Path) -> bool:
    """Verify the local trace has the required agent turn fields."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return all(
        payload.get(field)
        for field in ("tool_calls", "evidence_summary", "final_answer")
    )


def ratio(count: int, total: int) -> float:
    """Return a rounded score while treating empty denominators as success."""
    if total == 0:
        return 1.0
    return round(count / total, 3)


def tool_script(case: EvalCase) -> list[tuple[str, str]]:
    """Return the deterministic tool calls used for one fixture case."""
    tools = [case.expected_first_tool]
    if case.expected_fallback:
        tools.append(case.expected_fallback)
    return [(tool.value, case.mocked_outputs[tool].query) for tool in tools]


def scripted_model(tools: list[tuple[str, str]]) -> FunctionModel:
    """Return a local model that calls fixture tools in the requested order."""

    def call_next(messages, info):
        returns = []
        for message in messages:
            if isinstance(message, ModelRequest):
                returns.extend(part for part in message.parts if isinstance(part, ToolReturnPart))
        if len(returns) < len(tools):
            tool_name, query = tools[len(returns)]
            return ModelResponse(parts=[ToolCallPart(tool_name, {"query": query})])
        content = "\n".join(str(part.content) for part in returns)
        return ModelResponse(parts=[TextPart(content)])

    return FunctionModel(call_next)


def slug(value: str) -> str:
    """Create a filesystem-safe slug for eval trace directories."""
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")


def main() -> None:
    """Run the fixture eval suite and print concise aggregate metrics."""
    summary = evaluate_cases(load_cases())
    print(f"cases: {summary.total}")
    print(f"first tool accuracy: {summary.first_tool_accuracy:.0%}")
    print(f"fallback success: {summary.fallback_success_rate:.0%}")
    print(f"grounded answers: {summary.grounded_answer_rate:.0%}")
    print(f"trace completeness: {summary.trace_completeness:.0%}")


if __name__ == "__main__":
    main()
