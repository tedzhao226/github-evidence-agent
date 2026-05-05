import argparse
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from cloudbees_agent.agent import AgentSession
from cloudbees_agent.models import FinalAnswer
from cloudbees_agent.repo import find_repo
from cloudbees_agent.traceability import new_session_id
from cloudbees_agent.tools import GitHubEvidenceTools


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for chat and one-shot modes."""
    parser = argparse.ArgumentParser(description="Chat with a GitHub repository evidence agent.")
    parser.add_argument("prompt", nargs="?", help="Question containing a GitHub repo; omit for chat mode")
    parser.add_argument("--clone-root", help="Directory where code search should place fresh clones")
    parser.add_argument("--prompt-config", help="YAML file containing agent prompt layers")
    return parser


def format_answer(answer: FinalAnswer) -> str:
    """Render one agent answer for terminal output."""
    refs = "\n".join(f"- {ref}" for ref in answer.evidence_refs) or "- none"
    tools = ", ".join(tool.value for tool in answer.tool_calls) or "none"
    return (
        f"Tool calls: {tools}\n\n"
        f"Answer:\n{answer.answer}\n\n"
        f"Evidence refs:\n{refs}\n\n"
        f"Fallback used: {answer.fallback_used}\n"
        f"Session ID: {answer.session_id}\n"
        f"Trace JSON: {answer.trace_path}\n"
    )


def scoped_clone_root(args: argparse.Namespace, session_id: str) -> Path:
    """Return the session-specific clone root under the configured base root."""
    base_root = Path(args.clone_root or os.getenv("CLONE_ROOT", "tmp/repos"))
    return base_root / session_id


def build_session(repo: str, args: argparse.Namespace, session_id: str) -> AgentSession:
    """Create a repository chat session from parsed CLI arguments."""
    return AgentSession(
        repo=repo,
        tools=GitHubEvidenceTools(clone_root=scoped_clone_root(args, session_id)),
        prompt_config=Path(args.prompt_config) if args.prompt_config else None,
        session_id=session_id,
    )


def run_one_shot(args: argparse.Namespace) -> str:
    """Run one question and return the rendered terminal output."""
    repo = find_repo(args.prompt or "")
    if not repo:
        print(
            'Please mention a GitHub repo, for example: "For pydantic/pydantic-ai, how is tracing implemented?"',
            file=sys.stderr,
        )
        raise SystemExit(2)
    session_id = new_session_id()
    session = build_session(repo, args, session_id)
    output = format_answer(session.ask(args.prompt))
    print(output)
    return output


def run_chat(args: argparse.Namespace) -> None:
    """Run a small REPL that keeps conversation history until exit."""
    session: AgentSession | None = None
    active_repo: str | None = None
    session_id = new_session_id()
    print("Chat with a GitHub repo. Mention owner/name or a GitHub URL in your first question.")
    print('Example: "For pydantic/pydantic-ai, how is tracing implemented?"')
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
            print("Commands: /help, /clear, /exit, /quit")
            continue
        if question == "/clear":
            if session:
                session.clear()
                print(f"Conversation cleared for {session.repo}.")
            else:
                print("No active conversation.")
            continue
        repo = find_repo(question)
        if repo and repo != active_repo:
            session = build_session(repo, args, session_id)
            active_repo = repo
            print(f"Using repository: {repo}")
        if not session:
            print(
                'Please mention a GitHub repo, for example: "For pydantic/pydantic-ai, how is tracing implemented?"'
            )
            continue
        print(format_answer(session.ask(question)))


def main() -> None:
    """Load local env and run either one-shot or chat mode."""
    load_dotenv()
    args = build_parser().parse_args()
    if args.prompt:
        run_one_shot(args)
        return
    run_chat(args)


if __name__ == "__main__":
    main()
