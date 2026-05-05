from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import re
from uuid import uuid4

import logfire

from cloudbees_agent.models import EvidenceResult, LocalTrace, ToolName
from cloudbees_agent.settings import AppSettings


def configure_logfire(settings: AppSettings) -> None:
    """Configure Logfire export while keeping terminal logs off by default."""
    try:
        logfire.configure(
            send_to_logfire=bool(settings.logfire_token),
            token=settings.logfire_token,
            console=logfire_console_option(settings),
        )
    except Exception:
        return


def logfire_console_option(settings: AppSettings) -> None | bool:
    """Return the Logfire console option."""
    if settings.logfire_console:
        return None
    return False


@contextmanager
def trace_span(name: str, **attributes: object) -> Iterator[None]:
    """Wrap a Logfire span so callers do not import Logfire directly."""
    with logfire.span(name, **attributes):
        yield


def new_session_id() -> str:
    """Create a short id for one CLI conversation session."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


def judge_fallback(evidence: list[EvidenceResult]) -> tuple[bool, ToolName | None, str | None]:
    """Infer whether later tool calls recovered from weak first evidence."""
    if len(evidence) < 2 or evidence[0].items:
        return False, None, None
    return True, evidence[1].tool, f"{evidence[0].tool.value} returned no relevant evidence."


def trace_path(trace_dir: Path, session_id: str, repo: str, turn: int) -> Path:
    """Create a timestamped local trace path for one conversation turn."""
    safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return trace_dir / session_id / f"{safe_repo}-turn-{turn}-{stamp}.json"


def write_local_trace(
    path: Path,
    repo: str,
    question: str,
    session_id: str,
    tool_calls: list[ToolName],
    evidence: list[EvidenceResult],
    final_answer: str,
    fallback_tool: ToolName | None,
    fallback_reason: str | None,
    conversation_turn: int,
) -> LocalTrace:
    """Write the durable JSON trace for one agent turn and return the payload."""
    trace = LocalTrace(
        repo=repo,
        question=question,
        session_id=session_id,
        tool_calls=tool_calls,
        evidence_summary=evidence_summary(evidence),
        final_answer=final_answer,
        evidence=evidence,
        fallback_tool=fallback_tool,
        fallback_reason=fallback_reason,
        conversation_turn=conversation_turn,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")
    return trace


def record_turn_trace(
    settings: AppSettings,
    path: Path,
    repo: str,
    question: str,
    session_id: str,
    tool_calls: list[ToolName],
    evidence: list[EvidenceResult],
    final_answer: str,
    fallback_tool: ToolName | None,
    fallback_reason: str | None,
    conversation_turn: int,
) -> None:
    """Write local and/or remote traces from parsed settings."""
    backend = settings.trace_backend
    trace_kwargs = dict(
        repo=repo,
        question=question,
        session_id=session_id,
        tool_calls=tool_calls,
        evidence=evidence,
        final_answer=final_answer,
        fallback_tool=fallback_tool,
        fallback_reason=fallback_reason,
        conversation_turn=conversation_turn,
    )

    if backend in {"both", "local"}:
        payload = write_local_trace(path=path, **trace_kwargs)
    else:
        payload = LocalTrace(evidence_summary=evidence_summary(evidence), **trace_kwargs)

    if backend not in {"both", "remote"}:
        return
    if not settings.logfire_token:
        return

    try:
        logfire.info("agent.turn.trace", trace=payload.model_dump())
    except Exception:
        return


def evidence_summary(evidence: list[EvidenceResult]) -> str:
    """Join tool summaries into the compact text stored in local traces."""
    return " | ".join(item.summary for item in evidence if item.summary)
