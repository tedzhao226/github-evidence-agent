from contextlib import nullcontext
import json

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel

from cloudbees_agent.agent import AgentSession, ToolCallRecorder
from cloudbees_agent.models import EvidenceItem, EvidenceResult, ToolName
from cloudbees_agent.settings import AppSettings


class FakeTools:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def _lookup(self, tool, repo, query):
        self.calls.append((tool, repo, query))
        return self.results[tool]

    def readme(self, repo, query):
        return self._lookup(ToolName.README, repo, query)

    def issues(self, repo, query):
        return self._lookup(ToolName.ISSUES, repo, query)

    def commits(self, repo, query):
        return self._lookup(ToolName.COMMITS, repo, query)

    def code_search(self, repo, query):
        return self._lookup(ToolName.CODE_SEARCH, repo, query)


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


def settings_for_test() -> AppSettings:
    return AppSettings(
        _env_file=None,
        openai_api_key=None,
        logfire_token=None,
        github_token=None,
    )


def readme_each_turn_model() -> FunctionModel:
    def call_next(messages, info):
        latest_request = next(
            message for message in reversed(messages) if isinstance(message, ModelRequest)
        )
        if any(isinstance(part, ToolReturnPart) for part in latest_request.parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart("readme", {"query": "tracing"})])

    return FunctionModel(call_next)


def tool_sequence_model(tool_queries: list[tuple[str, str]]) -> FunctionModel:
    def call_next(messages, info):
        returns = []
        for message in messages:
            if isinstance(message, ModelRequest):
                returns.extend(part for part in message.parts if isinstance(part, ToolReturnPart))
        if len(returns) < len(tool_queries):
            tool_name, query = tool_queries[len(returns)]
            return ModelResponse(parts=[ToolCallPart(tool_name, {"query": query})])
        content = "\n".join(str(part.content) for part in returns)
        return ModelResponse(parts=[TextPart(content)])

    return FunctionModel(call_next)


def test_call_tool_records_evidence_and_returns_model_text(monkeypatch, tmp_path):
    monkeypatch.setattr("cloudbees_agent.agent.trace_span", lambda *args, **kwargs: nullcontext())
    result = evidence(ToolName.README, "README documents Logfire tracing.")
    tools = FakeTools({ToolName.README: result})
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=tool_sequence_model([("readme", "tracing")]),
        session_id="test-session",
    )
    recorder = ToolCallRecorder(repo="pydantic/pydantic-ai")

    model_text = session._call_tool(ToolName.README, "tracing", recorder)

    assert "README documents Logfire tracing." in model_text
    assert tools.calls == [(ToolName.README, "pydantic/pydantic-ai", "tracing")]
    assert recorder.evidence == [result]


def test_agent_session_builds_plain_closure_tools_without_deps_type(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
        }
    )
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=tool_sequence_model([("readme", "observability")]),
        session_id="test-session",
    )
    agent = session._build_pydantic_agent("pydantic/pydantic-ai", ToolCallRecorder("pydantic/pydantic-ai"))

    assert agent._deps_type is type(None)


def test_agent_session_records_tool_evidence_and_trace(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
        }
    )
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=tool_sequence_model([("readme", "observability tracing")]),
        session_id="test-session",
    )

    result = session.ask("pydantic/pydantic-ai", "How is observability supported?")

    assert tools.calls == [(ToolName.README, "pydantic/pydantic-ai", "observability tracing")]
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


def test_agent_session_prints_running_tool(capsys, tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
        }
    )
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=lambda: tool_sequence_model([("readme", "tracing")]),
        session_id="test-session",
    )

    session.ask("pydantic/pydantic-ai", "How is observability supported?")

    assert "Running tool: readme" in capsys.readouterr().out


def test_agent_session_surfaces_multiple_tool_calls(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
            ToolName.ISSUES: evidence(ToolName.ISSUES, "Issue discusses tracing."),
        }
    )
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=tool_sequence_model([("readme", "observability"), ("issues", "tracing issue")]),
        session_id="test-session",
    )

    result = session.ask("pydantic/pydantic-ai", "How is observability supported?")

    assert set(result.tool_calls) == {ToolName.README, ToolName.ISSUES}


def test_agent_session_uses_trace_recording_entrypoint(monkeypatch, tmp_path):
    calls = []
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
        }
    )
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=tool_sequence_model([("readme", "observability")]),
        session_id="test-session",
    )

    def fake_record_turn_trace(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("cloudbees_agent.agent.record_turn_trace", fake_record_turn_trace)

    session.ask("pydantic/pydantic-ai", "How is observability supported?")

    assert len(calls) == 1


def test_agent_session_separates_code_refs_from_evidence_refs(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
            ToolName.CODE_SEARCH: evidence(ToolName.CODE_SEARCH, "routing implementation."),
        }
    )
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=tool_sequence_model([("readme", "observability"), ("code_search", "routing")]),
        session_id="test-session",
    )

    result = session.ask("pydantic/pydantic-ai", "How is observability supported?")

    assert result.evidence_refs == ["https://github.com/example/repo/readme"]
    assert result.code_refs == ["https://github.com/example/repo/code_search"]


def test_agent_session_empty_tool_query_records_empty_evidence_without_backend_call(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
        }
    )
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=tool_sequence_model([("readme", "")]),
        session_id="test-session",
    )

    result = session.ask("pydantic/pydantic-ai", "How is observability supported?")

    assert tools.calls == []
    assert result.tool_calls == [ToolName.README]
    assert result.evidence_refs == []


def test_agent_session_keeps_history_across_turns_and_can_clear(tmp_path):
    tools = FakeTools(
        {
            ToolName.README: evidence(ToolName.README, "README documents Logfire tracing."),
        }
    )
    session = AgentSession(
        tools=tools,
        settings=settings_for_test(),
        trace_dir=tmp_path,
        model=readme_each_turn_model(),
        session_id="test-session",
    )

    first = session.ask("pydantic/pydantic-ai", "How is observability supported?")
    history_after_first = len(session.message_history)
    second = session.ask("fastapi/fastapi", "Where is that documented?")

    assert first.trace_path != second.trace_path
    assert "pydantic-pydantic-ai" in first.trace_path.name
    assert "fastapi-fastapi" in second.trace_path.name
    assert len(session.message_history) > history_after_first
    assert session.turn == 2
    assert tools.calls == [
        (ToolName.README, "pydantic/pydantic-ai", "tracing"),
        (ToolName.README, "fastapi/fastapi", "tracing"),
    ]

    session.clear()

    assert session.message_history == []
    assert session.turn == 0
