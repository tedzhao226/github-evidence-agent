import argparse
from pathlib import Path
import sys

from cloudbees_agent.agent import AgentSession
from cloudbees_agent.models import FinalAnswer
from cloudbees_agent.repo import find_repo
from cloudbees_agent.settings import AppSettings, load_settings
from cloudbees_agent.traceability import new_session_id
from cloudbees_agent.tools import compact_code_refs
from cloudbees_agent.tools import GitHubEvidenceTools


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for chat and one-shot modes."""
    parser = argparse.ArgumentParser(description="Chat with a GitHub repository evidence agent.")
    parser.add_argument("prompt", nargs="?", help="Question containing a GitHub repo; omit for chat mode")
    parser.add_argument("--clone-root", help="Directory where code search should place fresh clones")
    parser.add_argument("--sandbox-root", help="Directory where session sandbox clones are synced")
    parser.add_argument("--prompt-config", help="YAML file containing agent prompt layers")
    return parser


COMMAND_HELP = [
    ("/help", "show this message"),
    ("/clear", "clear this session's conversation history"),
    ("/exit", "leave chat"),
    ("/quit", "leave chat"),
]

CHAT_HINTS = [
    "For fastapi/fastapi, what is this repository about?",
    "For fastapi/fastapi, where is routing implemented?",
    "For fastapi/fastapi, what recent issues mention tracing, and what commits touched related code paths?",
]


def _print_help() -> None:
    print("Commands:")
    for command, detail in COMMAND_HELP:
        print(f"  {command} - {detail}")


def _print_chat_hints() -> None:
    print("Try these example prompts:")
    for i, prompt in enumerate(CHAT_HINTS, start=1):
        print(f"  {i}. {prompt}")


def format_answer(answer: FinalAnswer) -> str:
    """Render one agent answer for terminal output."""
    refs = "\n".join(f"- {ref}" for ref in answer.evidence_refs) or "- none"
    code_refs = compact_code_refs(answer.code_refs)
    code_refs_text = "\n".join(f"- {ref}" for ref in code_refs) or "- none"
    tools = ", ".join(tool.value for tool in answer.tool_calls) or "none"
    return (
        f"Tool calls: {tools}\n\n"
        f"Answer:\n{answer.answer}\n\n"
        f"Evidence refs:\n{refs}\n\n"
        f"Code refs:\n{code_refs_text}\n\n"
        f"Fallback used: {answer.fallback_used}\n"
        f"Session ID: {answer.session_id}\n"
        f"Trace JSON: {answer.trace_path}\n"
    )


def scoped_clone_root(args: argparse.Namespace, settings: AppSettings) -> Path:
    """Return the shared cache root for repo clones."""
    if args.clone_root:
        return Path(args.clone_root)
    return settings.clone_root


def scoped_sandbox_root(args: argparse.Namespace, settings: AppSettings, session_id: str) -> Path:
    """Return the session-specific sandbox root for repo sync."""
    base_root = Path(args.sandbox_root) if args.sandbox_root else settings.sandbox_root
    return base_root / session_id / "repos"


def build_session(args: argparse.Namespace, settings: AppSettings, session_id: str) -> AgentSession:
    """Create a chat session from parsed CLI arguments."""
    return AgentSession(
        tools=GitHubEvidenceTools(
            clone_root=scoped_clone_root(args, settings),
            sandbox_root=scoped_sandbox_root(args, settings, session_id),
            github_token=settings.github_token,
        ),
        settings=settings,
        prompt_config=Path(args.prompt_config) if args.prompt_config else settings.prompt_config,
        session_id=session_id,
    )


def run_one_shot(args: argparse.Namespace, settings: AppSettings | None = None) -> str:
    """Run one question and return the rendered terminal output."""
    settings = settings or load_settings()
    repo = find_repo(args.prompt or "")
    if not repo:
        print(
            'Please mention a GitHub repo, for example: "For fastapi/fastapi, how is tracing implemented?"',
            file=sys.stderr,
        )
        raise SystemExit(2)
    session_id = new_session_id()
    session = build_session(args, settings, session_id)
    output = format_answer(session.ask(repo, args.prompt))
    print(output)
    return output


def run_chat(args: argparse.Namespace, settings: AppSettings | None = None) -> None:
    """Run a small REPL that keeps conversation history until exit."""
    settings = settings or load_settings()
    session_id = new_session_id()
    session = build_session(args, settings, session_id)
    active_repo: str | None = None
    print("Chat with a GitHub repo. Mention owner/name or a GitHub URL in your first question.")
    print('Example: "For fastapi/fastapi, how is tracing implemented?"')
    _print_chat_hints()
    print(f"Session ID: {session_id}")
    print("Type /help for commands.")
    while True:
        try:
            question = input("cloudbees-agent> ").strip()
        except EOFError:
            print()
            break
        if not question:
            continue
        if question in {"/exit", "/quit"}:
            break
        if question == "/help":
            _print_help()
            _print_chat_hints()
            continue
        if question == "/clear":
            if active_repo:
                session.clear()
                print("Conversation cleared.")
            else:
                print("No active conversation.")
            continue
        if question.startswith("/"):
            print(f'Unknown command: "{question}". Type /help for available commands.')
            continue
        repo = find_repo(question)
        if repo and repo != active_repo:
            active_repo = repo
            print(f"Using repository: {repo}")
        if not active_repo:
            print(
                'Please mention a GitHub repo, for example: "For fastapi/fastapi, how is tracing implemented?"'
            )
            continue
        print(format_answer(session.ask(active_repo, question)))


def main() -> None:
    """Load local env and run either one-shot or chat mode."""
    args = build_parser().parse_args()
    settings = load_settings()
    if args.prompt:
        run_one_shot(args, settings)
        return
    run_chat(args, settings)


if __name__ == "__main__":
    main()
