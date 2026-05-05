import os

from dotenv import load_dotenv
import pytest

from cloudbees_agent.cli import (
    build_parser,
    build_session,
    format_answer,
    run_chat,
    run_one_shot,
    scoped_clone_root,
)
from cloudbees_agent.models import FinalAnswer, ToolName


def test_dotenv_loads_without_overriding_exported_values(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=from-file\nOPENAI_MODEL=openai:gpt-5-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    load_dotenv(env_path)

    assert os.environ["OPENAI_API_KEY"] == "from-shell"
    assert os.environ["OPENAI_MODEL"] == "openai:gpt-5-mini"


def test_cli_parser_uses_no_args_for_chat_mode():
    args = build_parser().parse_args([])

    assert args.prompt is None


def test_cli_parser_accepts_one_shot_prompt():
    args = build_parser().parse_args(
        ["For pydantic/pydantic-ai, where is tracing?"]
    )

    assert args.prompt == "For pydantic/pydantic-ai, where is tracing?"


def test_cli_parser_rejects_removed_repo_and_question_flags():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--repo", "pydantic/pydantic-ai"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--question", "Where is tracing?"])


def test_cli_parser_rejects_removed_workflow_config_flag():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["For pydantic/pydantic-ai, where is tracing?", "--workflow-config", "workflow.yaml"]
        )


def test_cli_parser_rejects_removed_sample_output_flag():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["For pydantic/pydantic-ai, where is tracing?", "--sample-output", "sample_run.txt"]
        )


def test_scoped_clone_root_uses_session_id_under_base_root(tmp_path):
    args = build_parser().parse_args(["--clone-root", str(tmp_path)])

    assert scoped_clone_root(args, "session-123") == tmp_path / "session-123"


def test_build_session_uses_scoped_clone_root_and_session_id(tmp_path):
    args = build_parser().parse_args(["--clone-root", str(tmp_path)])

    session = build_session("pydantic/pydantic-ai", args, "session-123")

    assert session.session_id == "session-123"
    assert session.tools.clone_root == tmp_path / "session-123"


def test_chat_loop_detects_repo_reuses_session_and_handles_clear(monkeypatch, capsys):
    class FakeSession:
        def __init__(self, repo, session_id):
            self.repo = repo
            self.session_id = session_id
            self.cleared = False
            self.questions = []

        def clear(self):
            self.cleared = True

        def ask(self, question):
            self.questions.append(question)
            return FinalAnswer(
                answer="ok",
                tool_calls=[ToolName.README],
                trace_path="trace.json",
                session_id=self.session_id,
            )

    sessions = []
    args = build_parser().parse_args([])
    inputs = iter(
        [
            "/help",
            "For pydantic/pydantic-ai, where is tracing?",
            "Where exactly?",
            "/clear",
            "/exit",
        ]
    )
    monkeypatch.setattr("cloudbees_agent.cli.new_session_id", lambda: "session-123")
    monkeypatch.setattr(
        "cloudbees_agent.cli.build_session",
        lambda repo, parsed, session_id: sessions.append(FakeSession(repo, session_id)) or sessions[-1],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    run_chat(args)

    output = capsys.readouterr().out
    assert 'Example: "For pydantic/pydantic-ai, how is tracing implemented?"' in output
    assert "Session ID: session-123" in output
    assert "Commands: /help, /clear, /exit, /quit" in output
    assert "Using repository: pydantic/pydantic-ai" in output
    assert "Conversation cleared for pydantic/pydantic-ai." in output
    assert len(sessions) == 1
    assert sessions[0].questions == [
        "For pydantic/pydantic-ai, where is tracing?",
        "Where exactly?",
    ]
    assert sessions[0].cleared is True
    assert sessions[0].session_id == "session-123"


def test_chat_loop_asks_for_repo_before_session_exists(monkeypatch, capsys):
    args = build_parser().parse_args([])
    inputs = iter(["Where is tracing?", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    run_chat(args)

    assert "Please mention a GitHub repo" in capsys.readouterr().out


def test_chat_loop_switches_session_when_new_repo_is_mentioned(monkeypatch):
    class FakeSession:
        def __init__(self, repo, session_id):
            self.repo = repo
            self.session_id = session_id

        def ask(self, question):
            return FinalAnswer(answer="ok", trace_path="trace.json", session_id=self.session_id)

    sessions = []
    args = build_parser().parse_args([])
    inputs = iter(
        [
            "For pydantic/pydantic-ai, where is tracing?",
            "For fastapi/fastapi, where is routing?",
            "/exit",
        ]
    )
    monkeypatch.setattr("cloudbees_agent.cli.new_session_id", lambda: "session-123")
    monkeypatch.setattr(
        "cloudbees_agent.cli.build_session",
        lambda repo, parsed, session_id: sessions.append(FakeSession(repo, session_id)) or sessions[-1],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    run_chat(args)

    assert [session.repo for session in sessions] == ["pydantic/pydantic-ai", "fastapi/fastapi"]
    assert [session.session_id for session in sessions] == ["session-123", "session-123"]


def test_one_shot_without_repo_exits_with_helpful_error(capsys):
    args = build_parser().parse_args(["Where is tracing?"])

    with pytest.raises(SystemExit) as exc:
        run_one_shot(args)

    assert exc.value.code == 2
    assert "Please mention a GitHub repo" in capsys.readouterr().err


def test_format_answer_prints_all_tool_calls(tmp_path):
    answer = FinalAnswer(
        answer="Tracing is documented.",
        evidence_refs=["https://github.com/example/repo#readme"],
        tool_calls=[ToolName.README, ToolName.CODE_SEARCH],
        trace_path=tmp_path / "trace.json",
        session_id="session-123",
    )

    output = format_answer(answer)

    assert "Tool calls: readme, code_search" in output
    assert "Session ID: session-123" in output
    assert "Selected tool:" not in output
