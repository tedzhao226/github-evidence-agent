import json

from cloudbees_agent.models import EvidenceItem, EvidenceResult, ToolName
from cloudbees_agent.traceability import (
    configure_logfire,
    evidence_refs,
    judge_fallback,
    trace_path,
    write_local_trace,
)


def evidence(tool: ToolName, text: str) -> EvidenceResult:
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


def test_judge_fallback_marks_later_tool_when_first_evidence_is_empty():
    evidence_results = [
        evidence(ToolName.ISSUES, ""),
        evidence(ToolName.README, "README documents Logfire tracing."),
    ]

    fallback_used, fallback_tool, fallback_reason = judge_fallback(evidence_results)

    assert fallback_used is True
    assert fallback_tool == ToolName.README
    assert fallback_reason == "issues returned no relevant evidence."


def test_evidence_refs_collects_urls_paths_and_titles():
    result = EvidenceResult(
        tool=ToolName.CODE_SEARCH,
        query="trace",
        summary="found trace helper",
        items=[
            EvidenceItem(kind="code", title="with url", url="https://example.test/ref", excerpt="one"),
            EvidenceItem(kind="code", title="with path", path="src/ref.py", excerpt="two"),
            EvidenceItem(kind="code", title="title only", excerpt="three"),
        ],
    )

    refs = evidence_refs([result])

    assert refs == ["https://example.test/ref", "src/ref.py", "title only"]


def test_write_local_trace_records_turn_metadata(tmp_path):
    path = trace_path(tmp_path, "session-123", "owner/repo", 2)

    write_local_trace(
        path=path,
        repo="owner/repo",
        question="Where is tracing?",
        session_id="session-123",
        tool_calls=[ToolName.README],
        evidence=[evidence(ToolName.README, "README documents tracing.")],
        final_answer="Tracing is documented in README.",
        fallback_tool=None,
        fallback_reason=None,
        conversation_turn=2,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.parent.name == "session-123"
    assert payload["repo"] == "owner/repo"
    assert payload["session_id"] == "session-123"
    assert payload["conversation_turn"] == 2
    assert payload["tool_calls"] == ["readme"]
    assert payload["evidence_summary"] == "README documents tracing."


def test_configure_logfire_disables_console_by_default(monkeypatch):
    calls = []
    monkeypatch.delenv("LOGFIRE_CONSOLE", raising=False)
    monkeypatch.setattr("logfire.configure", lambda **kwargs: calls.append(kwargs))

    configure_logfire()

    assert calls == [{"send_to_logfire": "if-token-present", "console": False}]


def test_configure_logfire_allows_console_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setenv("LOGFIRE_CONSOLE", "true")
    monkeypatch.setattr("logfire.configure", lambda **kwargs: calls.append(kwargs))

    configure_logfire()

    assert calls == [{"send_to_logfire": "if-token-present", "console": None}]
