import json
import os

from dotenv import load_dotenv
import logfire
import pytest

from cloudbees_agent.agent import AgentSession
from cloudbees_agent.tools import GitHubEvidenceTools


SAMPLE_REPO = "pydantic/pydantic-ai"
SAMPLE_QUESTION = (
    "How does this repository support observability or tracing, "
    "and where is that implemented?"
)


def require_env(name: str) -> None:
    if not os.getenv(name):
        pytest.skip(f"{name} is required for this smoke test")


@pytest.mark.smoke
def test_smoke_openai_agent_turn_uses_real_model(tmp_path):
    load_dotenv()
    require_env("OPENAI_API_KEY")

    session = AgentSession(
        repo=SAMPLE_REPO,
        tools=GitHubEvidenceTools(),
        trace_dir=tmp_path,
    )
    answer = session.ask(SAMPLE_QUESTION)

    assert answer.answer.strip()
    assert answer.evidence_refs


@pytest.mark.smoke
def test_smoke_agent_run_writes_trace_and_flushes_logfire(tmp_path):
    load_dotenv()
    require_env("OPENAI_API_KEY")
    require_env("LOGFIRE_TOKEN")

    session = AgentSession(repo=SAMPLE_REPO, tools=GitHubEvidenceTools(), trace_dir=tmp_path)
    answer = session.ask(SAMPLE_QUESTION)
    follow_up = session.ask("Can you point to the most relevant source again?")
    flushed = logfire.force_flush(timeout_millis=30000)

    assert answer.answer.strip()
    assert follow_up.answer.strip()
    assert answer.tool_calls
    assert answer.evidence_refs
    assert answer.trace_path.exists()
    assert len(session.message_history) > 0
    assert isinstance(flushed, bool)

    payload = json.loads(answer.trace_path.read_text(encoding="utf-8"))
    assert payload["tool_calls"]
    assert payload["evidence_summary"]
    assert payload["final_answer"]
