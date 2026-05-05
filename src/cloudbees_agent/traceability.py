from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
import re
from uuid import uuid4

import logfire

from cloudbees_agent.models import EvidenceResult, LocalTrace, ToolName


def configure_logfire() -> None:
    """Configure Logfire export while keeping terminal logs off by default."""
    try:
        logfire.configure(
            send_to_logfire="if-token-present",
            console=logfire_console_option(),
        )
    except Exception:
        return


def logfire_console_option() -> None | bool:
    """Return the Logfire console option from LOGFIRE_CONSOLE."""
    value = os.getenv("LOGFIRE_CONSOLE", "false").lower()
    if value in {"1", "true", "yes", "on"}:
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
    """Collect stable references from evidence items for CLI output."""
    refs = []
    for result in evidence:
        for item in result.items:
            ref = item.url or item.path or item.title
            if ref:
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


def evidence_summary(evidence: list[EvidenceResult]) -> str:
    """Join tool summaries into the compact text stored in local traces."""
    return " | ".join(item.summary for item in evidence if item.summary)
