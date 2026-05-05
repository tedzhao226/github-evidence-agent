from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import re
from urllib.parse import unquote, urlparse
from uuid import uuid4

import logfire

from cloudbees_agent.models import EvidenceResult, LocalTrace, ToolName
from cloudbees_agent.settings import AppSettings


def _github_file_line_ref(ref: str) -> tuple[str, str] | None:
    """Return repository-relative file path and line from a GitHub blob URL."""
    parsed = urlparse(ref)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != "github.com":
        return None
    parts = parsed.path.split("/")
    if len(parts) < 6 or parts[3] != "blob" or parts[4] != "HEAD":
        return None
    if not parsed.fragment.startswith("L"):
        return None
    fragment = parsed.fragment
    if fragment.startswith("L") and "-" in fragment:
        line = fragment[1:].split("-", 1)[0]
    else:
        line = parsed.fragment[1:]
    if not line.isdigit():
        return None
    rel_path = "/".join(parts[5:])
    if not rel_path:
        return None
    return unquote(rel_path), line


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


def evidence_refs(evidence: list[EvidenceResult]) -> list[str]:
    """Collect stable, de-duplicated references from evidence items."""
    refs = []
    seen: set[str] = set()
    for result in evidence:
        for item in result.items:
            ref = item.url or item.path or item.title
            if not ref:
                continue
            key = _github_file_line_ref(ref)
            dedupe_key = key[0] if key else ref
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            refs.append(ref)
    return refs


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
) -> None:
    """Write the durable JSON trace for one agent turn."""
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


def _make_trace_payload(
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
    """Build a shared LocalTrace payload for local and remote persistence."""
    return LocalTrace(
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
    payload = _make_trace_payload(
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
        write_local_trace(
            path=path,
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
