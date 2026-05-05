import json

import logfire
import pytest

from cloudbees_agent.agent import AgentSession
from cloudbees_agent.settings import AppSettings, load_settings
from cloudbees_agent.tools import GitHubEvidenceTools


SAMPLE_REPO = "pydantic/pydantic-ai"
SAMPLE_QUESTION = (
    "How does this repository support observability or tracing, "
    "and where is that implemented?"
)


def require_setting(value: str | None, name: str) -> None:
    if not value:
        pytest.skip(f"{name} is required for this smoke test")


def smoke_tools(settings: AppSettings) -> GitHubEvidenceTools:
    return GitHubEvidenceTools(github_token=settings.github_token)


@pytest.mark.smoke
def test_smoke_openai_agent_turn_uses_real_model(tmp_path):
    settings = load_settings()
    require_setting(settings.openai_api_key, "OPENAI_API_KEY")

    session = AgentSession(
        tools=smoke_tools(settings),
        settings=settings,
        trace_dir=tmp_path,
    )
    answer = session.ask(SAMPLE_REPO, SAMPLE_QUESTION)

    assert answer.answer.strip()
    assert answer.evidence_refs or answer.code_refs


@pytest.mark.smoke
def test_smoke_agent_run_writes_trace_and_flushes_logfire(tmp_path):
    settings = load_settings()
    require_setting(settings.openai_api_key, "OPENAI_API_KEY")
    require_setting(settings.logfire_token, "LOGFIRE_TOKEN")

    session = AgentSession(tools=smoke_tools(settings), settings=settings, trace_dir=tmp_path)
    answer = session.ask(SAMPLE_REPO, SAMPLE_QUESTION)
    follow_up = session.ask(SAMPLE_REPO, "Can you point to the most relevant source again?")
    flushed = logfire.force_flush(timeout_millis=30000)

    assert answer.answer.strip()
    assert follow_up.answer.strip()
    assert answer.tool_calls
    assert answer.evidence_refs or answer.code_refs
    assert answer.trace_path.exists()
    assert len(session.message_history) > 0
    assert isinstance(flushed, bool)

    payload = json.loads(answer.trace_path.read_text(encoding="utf-8"))
    assert payload["tool_calls"]
    assert payload["evidence_summary"]
    assert payload["final_answer"]
