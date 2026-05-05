from pathlib import Path

import pytest

from cloudbees_agent.cli import (
    build_parser,
    build_session,
    format_answer,
    run_chat,
    run_one_shot,
    scoped_clone_root,
    scoped_sandbox_root,
)
from cloudbees_agent.models import FinalAnswer, ToolName
from cloudbees_agent.settings import AppSettings


def settings_for_test(tmp_path: Path | None = None) -> AppSettings:
    return AppSettings(
        _env_file=None,
        openai_api_key=None,
        logfire_token=None,
        github_token=None,
        clone_root=(tmp_path / "repos") if tmp_path else Path("tmp/repos"),
        sandbox_root=(tmp_path / "sandbox") if tmp_path else Path("tmp/sandbox/sessions"),
    )


def test_cli_parser_uses_no_args_for_chat_mode():
    args = build_parser().parse_args([])

    assert args.prompt is None


def test_cli_parser_accepts_one_shot_prompt():
    args = build_parser().parse_args(
        ["For pydantic/pydantic-ai, where is tracing?"]
    )

    assert args.prompt == "For pydantic/pydantic-ai, where is tracing?"


def test_scoped_clone_root_uses_base_root(tmp_path):
    args = build_parser().parse_args(["--clone-root", str(tmp_path)])

    assert scoped_clone_root(args, settings_for_test()) == tmp_path


def test_build_session_uses_scoped_clone_root_and_session_id(tmp_path):
    args = build_parser().parse_args(["--clone-root", str(tmp_path)])

    session = build_session(args, settings_for_test(), "session-123")

    assert session.session_id == "session-123"
    assert session.tools.clone_root == tmp_path
    assert session.tools.sandbox_root == Path("tmp/sandbox/sessions/session-123/repos")


def test_scoped_sandbox_root_uses_session_root_and_repo_folder():
    args = build_parser().parse_args(["--sandbox-root", "/tmp/sandbox"])

    assert scoped_sandbox_root(args, settings_for_test(), "session-123") == (
        Path("/tmp/sandbox") / "session-123" / "repos"
    )


def test_chat_loop_detects_repo_reuses_session_and_handles_clear(monkeypatch, capsys):
    class FakeSession:
        def __init__(self, session_id):
            self.session_id = session_id
            self.cleared = False
            self.questions = []

        def clear(self):
            self.cleared = True

        def ask(self, repo, question):
            self.questions.append((repo, question))
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
        lambda parsed, settings, session_id: sessions.append(FakeSession(session_id))
        or sessions[-1],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    run_chat(args, settings_for_test())

    output = capsys.readouterr().out
    assert 'Example: "For fastapi/fastapi, how is tracing implemented?"' in output
    assert "Session ID: session-123" in output
    assert "Commands:" in output
    assert "  /help - show this message" in output
    assert "  /clear - clear this session's conversation history" in output
    assert "  /exit - leave chat" in output
    assert "  /quit - leave chat" in output
    assert "Try these example prompts:" in output
    assert "  1. For fastapi/fastapi, what is this repository about?" in output
    assert (
        "  2. For fastapi/fastapi, where is routing implemented?"
        in output
    )
    assert "  3. For fastapi/fastapi, what recent issues mention tracing, and what commits touched related code paths?" in output
    assert "Using repository: pydantic/pydantic-ai" in output
    assert "Conversation cleared." in output
    assert len(sessions) == 1
    assert sessions[0].questions == [
        ("pydantic/pydantic-ai", "For pydantic/pydantic-ai, where is tracing?"),
        ("pydantic/pydantic-ai", "Where exactly?"),
    ]
    assert sessions[0].cleared is True
    assert sessions[0].session_id == "session-123"


def test_chat_loop_prints_unknown_command_hint(monkeypatch, capsys):
    args = build_parser().parse_args([])
    inputs = iter(["/helps", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    run_chat(args, settings_for_test())

    assert 'Unknown command: "/helps". Type /help for available commands.' in capsys.readouterr().out


def test_chat_loop_asks_for_repo_before_session_exists(monkeypatch, capsys):
    args = build_parser().parse_args([])
    inputs = iter(["Where is tracing?", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    run_chat(args, settings_for_test())

    assert "Please mention a GitHub repo" in capsys.readouterr().out


def test_chat_loop_switches_active_repo_without_rebuilding_session(monkeypatch):
    class FakeSession:
        def __init__(self, session_id):
            self.session_id = session_id
            self.questions = []

        def ask(self, repo, question):
            self.questions.append((repo, question))
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
        lambda parsed, settings, session_id: sessions.append(FakeSession(session_id))
        or sessions[-1],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    run_chat(args, settings_for_test())

    assert len(sessions) == 1
    assert sessions[0].session_id == "session-123"
    assert sessions[0].questions == [
        ("pydantic/pydantic-ai", "For pydantic/pydantic-ai, where is tracing?"),
        ("fastapi/fastapi", "For fastapi/fastapi, where is routing?"),
    ]


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
        code_refs=["https://github.com/example/repo/blob/HEAD/src/main.py#L10"],
        tool_calls=[ToolName.README, ToolName.CODE_SEARCH],
        trace_path=tmp_path / "trace.json",
        session_id="session-123",
    )

    output = format_answer(answer)

    assert "Tool calls: readme, code_search" in output
    assert "Evidence refs:\n- https://github.com/example/repo#readme" in output
    assert "Code refs:\n- src/main.py:10" in output
    assert "Session ID: session-123" in output
    assert "Selected tool:" not in output


def test_format_answer_compacts_code_refs():
    answer = FinalAnswer(
        answer="Routing is documented.",
        code_refs=[
            "https://github.com/fastapi/fastapi/blob/HEAD/fastapi/routing.py#L35",
            "https://github.com/fastapi/fastapi/blob/HEAD/fastapi/routing.py#L80",
            "https://github.com/fastapi/fastapi/blob/HEAD/fastapi/applications.py#L6",
        ],
        evidence_refs=["https://github.com/example/repo#readme"],
        tool_calls=[ToolName.CODE_SEARCH],
        trace_path="trace.json",
        session_id="session-123",
    )

    output = format_answer(answer)

    assert (
        "Code refs:\n- fastapi/routing.py:35\n- fastapi/applications.py:6"
        in output
    )
