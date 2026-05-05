import json

from cloudbees_agent.models import EvidenceItem, EvidenceResult, ToolName
from cloudbees_agent.settings import AppSettings
from cloudbees_agent.traceability import (
    configure_logfire,
    judge_fallback,
    record_turn_trace,
    trace_path,
    write_local_trace,
)


def settings_for_test(**overrides) -> AppSettings:
    values = {
        "openai_api_key": None,
        "logfire_token": None,
        "github_token": None,
    }
    values.update(overrides)
    return AppSettings(_env_file=None, **values)


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


def test_record_turn_trace_writes_local_and_skips_remote_without_token(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("logfire.info", lambda *args, **kwargs: calls.append((args, kwargs)))

    path = trace_path(tmp_path, "session-123", "owner/repo", 1)
    record_turn_trace(
        settings=settings_for_test(),
        path=path,
        repo="owner/repo",
        question="Where is tracing?",
        session_id="session-123",
        tool_calls=[ToolName.README],
        evidence=[evidence(ToolName.README, "README documents tracing.")],
        final_answer="Tracing is documented.",
        fallback_tool=None,
        fallback_reason=None,
        conversation_turn=1,
    )

    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["conversation_turn"] == 1
    assert not calls


def test_record_turn_trace_sends_remote_when_token_present(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("logfire.info", lambda *args, **kwargs: calls.append((args, kwargs)))

    path = trace_path(tmp_path, "session-123", "owner/repo", 1)
    record_turn_trace(
        settings=settings_for_test(logfire_token="token"),
        path=path,
        repo="owner/repo",
        question="Where is tracing?",
        session_id="session-123",
        tool_calls=[ToolName.README],
        evidence=[evidence(ToolName.README, "README documents tracing.")],
        final_answer="Tracing is documented.",
        fallback_tool=None,
        fallback_reason=None,
        conversation_turn=1,
    )

    assert path.exists()
    assert calls


def test_record_turn_trace_can_force_local_with_token(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("logfire.info", lambda *args, **kwargs: calls.append((args, kwargs)))

    path = trace_path(tmp_path, "session-123", "owner/repo", 1)
    record_turn_trace(
        settings=settings_for_test(logfire_token="token", trace_backend="local"),
        path=path,
        repo="owner/repo",
        question="Where is tracing?",
        session_id="session-123",
        tool_calls=[ToolName.README],
        evidence=[evidence(ToolName.README, "README documents tracing.")],
        final_answer="Tracing is documented.",
        fallback_tool=None,
        fallback_reason=None,
        conversation_turn=1,
    )

    assert path.exists()
    assert not calls


def test_record_turn_trace_can_force_remote_without_local(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("logfire.info", lambda *args, **kwargs: calls.append((args, kwargs)))

    path = trace_path(tmp_path, "session-123", "owner/repo", 1)
    record_turn_trace(
        settings=settings_for_test(logfire_token="token", trace_backend="remote"),
        path=path,
        repo="owner/repo",
        question="Where is tracing?",
        session_id="session-123",
        tool_calls=[ToolName.README],
        evidence=[evidence(ToolName.README, "README documents tracing.")],
        final_answer="Tracing is documented.",
        fallback_tool=None,
        fallback_reason=None,
        conversation_turn=1,
    )

    assert not path.exists()
    assert calls


def test_record_turn_trace_remote_backend_requires_token(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("logfire.info", lambda *args, **kwargs: calls.append((args, kwargs)))

    path = trace_path(tmp_path, "session-123", "owner/repo", 1)
    record_turn_trace(
        settings=settings_for_test(trace_backend="remote"),
        path=path,
        repo="owner/repo",
        question="Where is tracing?",
        session_id="session-123",
        tool_calls=[ToolName.README],
        evidence=[evidence(ToolName.README, "README documents tracing.")],
        final_answer="Tracing is documented.",
        fallback_tool=None,
        fallback_reason=None,
        conversation_turn=1,
    )

    assert not path.exists()
    assert not calls


def test_configure_logfire_disables_console_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr("logfire.configure", lambda **kwargs: calls.append(kwargs))

    configure_logfire(settings_for_test())

    assert calls == [{"send_to_logfire": False, "token": None, "console": False}]


def test_configure_logfire_allows_console_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr("logfire.configure", lambda **kwargs: calls.append(kwargs))

    configure_logfire(settings_for_test(logfire_console=True))

    assert calls == [{"send_to_logfire": False, "token": None, "console": None}]


def test_configure_logfire_sends_remote_when_token_present(monkeypatch):
    calls = []
    monkeypatch.setattr("logfire.configure", lambda **kwargs: calls.append(kwargs))

    configure_logfire(settings_for_test(logfire_token="token"))

    assert calls == [{"send_to_logfire": True, "token": "token", "console": False}]
