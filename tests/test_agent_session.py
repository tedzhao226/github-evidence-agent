import json

from pydantic_ai.models.test import TestModel

from cloudbees_agent.agent import AgentSession
from cloudbees_agent.models import EvidenceItem, EvidenceResult, ToolName


class FakeTools:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def run(self, tool, repo, question):
        self.calls.append((tool, question))
        return self.results[tool]


def evidence(tool, text):
    items = []
    if text:
        items.append(
            EvidenceItem(
                kind=tool.value,
                title=f"{tool.value} hit",
                url=f"https://github.com/example/repo/{tool.value}",
                excerpt=text,
            )
        )
    return EvidenceResult(tool=tool, query="observability", summary=text, items=items)


def test_agent_session_records_tool_evidence_and_trace(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
        }
    )
    session = AgentSession(
        repo="pydantic/pydantic-ai",
        tools=tools,
        trace_dir=tmp_path,
        model=TestModel(call_tools=["readme"]),
        session_id="test-session",
    )

    result = session.ask("How is observability supported?")

    assert [call[0] for call in tools.calls] == [ToolName.README]
    assert result.tool_calls == [ToolName.README]
    assert result.session_id == "test-session"
    assert "Logfire tracing" in result.answer
    assert result.trace_path.exists()
    assert result.trace_path.parent.name == "test-session"

    payload = json.loads(result.trace_path.read_text(encoding="utf-8"))
    assert payload["session_id"] == "test-session"
    assert payload["conversation_turn"] == 1
    assert payload["tool_calls"] == ["readme"]
    assert payload["evidence"][0]["tool"] == "readme"


def test_agent_session_surfaces_multiple_tool_calls(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
            ToolName.ISSUES: evidence(ToolName.ISSUES, "Issue discusses tracing."),
        }
    )
    session = AgentSession(
        repo="pydantic/pydantic-ai",
        tools=tools,
        trace_dir=tmp_path,
        model=TestModel(call_tools=["readme", "issues"]),
        session_id="test-session",
    )

    result = session.ask("How is observability supported?")

    assert set(result.tool_calls) == {ToolName.README, ToolName.ISSUES}


def test_agent_session_keeps_history_across_turns_and_can_clear(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
        }
    )
    session = AgentSession(
        repo="pydantic/pydantic-ai",
        tools=tools,
        trace_dir=tmp_path,
        model=TestModel(call_tools=["readme"]),
        session_id="test-session",
    )

    first = session.ask("How is observability supported?")
    history_after_first = len(session.message_history)
    second = session.ask("Where is that documented?")

    assert first.trace_path != second.trace_path
    assert len(session.message_history) > history_after_first
    assert session.turn == 2

    session.clear()

    assert session.message_history == []
    assert session.turn == 0
