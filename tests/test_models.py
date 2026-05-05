import json

import pytest
from pydantic import ValidationError

from cloudbees_agent.models import (
    EvidenceItem,
    EvidenceResult,
    LocalTrace,
    ToolName,
)
from cloudbees_agent.repo import find_repo, parse_repo


def test_parse_repo_accepts_owner_name_and_github_url():
    assert parse_repo("pydantic/pydantic-ai") == ("pydantic", "pydantic-ai")
    assert parse_repo("https://github.com/pydantic/pydantic-ai") == (
        "pydantic",
        "pydantic-ai",
    )


def test_find_repo_detects_owner_name_in_conversation():
    assert find_repo("For pydantic/pydantic-ai, how is tracing implemented?") == (
        "pydantic/pydantic-ai"
    )


def test_find_repo_detects_github_url_in_conversation():
    assert find_repo("Please inspect https://github.com/pydantic/pydantic-ai for tracing.") == (
        "pydantic/pydantic-ai"
    )


def test_find_repo_strips_trailing_punctuation():
    assert find_repo("Check https://github.com/pydantic/pydantic-ai.") == "pydantic/pydantic-ai"


def test_find_repo_returns_none_when_missing():
    assert find_repo("Where is tracing implemented?") is None


@pytest.mark.parametrize("repo", ["pydantic", "https://example.com/a/b", "a/b/c"])
def test_parse_repo_rejects_invalid_repositories(repo):
    with pytest.raises(ValueError):
        parse_repo(repo)


def test_trace_json_shape_contains_required_agent_turn_fields(tmp_path):
    trace = LocalTrace(
        repo="pydantic/pydantic-ai",
        question="Where is tracing implemented?",
        session_id="test-session",
        tool_calls=[ToolName.README, ToolName.CODE_SEARCH],
        evidence_summary="README mentions Logfire integration.",
        final_answer="Tracing is documented through Logfire references.",
        evidence=[
            EvidenceResult(
                tool=ToolName.README,
                query="tracing observability",
                summary="README mentions Logfire.",
                items=[
                    EvidenceItem(
                        kind="readme",
                        title="README",
                        url="https://github.com/pydantic/pydantic-ai",
                        excerpt="Logfire instrumentation is supported.",
                    )
                ],
            )
        ],
    )
    path = tmp_path / "trace.json"
    path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["session_id"] == "test-session"
    assert payload["tool_calls"] == ["readme", "code_search"]
    assert payload["evidence_summary"]
    assert payload["fallback_reason"] is None
    assert payload["final_answer"]
    assert payload["conversation_turn"] == 1


def test_evidence_result_requires_items_list():
    with pytest.raises(ValidationError):
        EvidenceResult(tool=ToolName.README, query="x", summary="x", items=None)
