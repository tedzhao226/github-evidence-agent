from pathlib import Path

from cloudbees_agent.tools import (
    GitHubEvidenceTools,
    clone_lock,
    clone_target_path,
    collect_code_matches,
    compact_code_refs,
    default_clone_root,
    default_sandbox_root,
    iter_source_files,
    query_terms,
    refresh_clone,
    sync_clone_to_sandbox,
    split_evidence_refs,
)
from cloudbees_agent.models import EvidenceItem, EvidenceResult, ToolName


def test_query_terms_uses_model_query_without_adding_synonyms():
    terms = query_terms("observability tracing")

    assert "observability" in terms
    assert "tracing" in terms
    assert "logfire" not in terms
    assert "trace" not in terms


def test_iter_source_files_ignores_vendor_and_build_dirs(tmp_path):
    keep = tmp_path / "src" / "agent.py"
    skip_vendor = tmp_path / "vendor" / "agent.py"
    skip_build = tmp_path / "build" / "agent.py"
    for path in (keep, skip_vendor, skip_build):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    files = list(iter_source_files(Path(tmp_path)))

    assert files == [keep]


def test_default_clone_root_is_repo_local_tmp_repos():
    root = default_clone_root()

    assert root == Path.cwd() / "tmp" / "repos"


def test_default_sandbox_root_is_repo_local_tmp_sandbox_sessions():
    root = default_sandbox_root()

    assert root == Path.cwd() / "tmp" / "sandbox" / "sessions"


def test_clone_target_path_uses_custom_root_and_repo_name(tmp_path):
    target = clone_target_path("pydantic/pydantic-ai", tmp_path)

    assert target == tmp_path / "pydantic-pydantic-ai"


def test_refresh_clone_removes_partial_clone_before_running_git(tmp_path, monkeypatch):
    target = tmp_path / "pydantic-pydantic-ai"
    target.mkdir()
    (target / "partial.txt").write_text("partial", encoding="utf-8")
    calls = []

    def fake_run(args, check, capture_output, text):
        calls.append(args)
        target.mkdir(exist_ok=True)
        (target / ".git").mkdir()

    monkeypatch.setattr("subprocess.run", fake_run)

    refresh_clone("https://github.com/pydantic/pydantic-ai.git", target)

    assert calls == [
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "https://github.com/pydantic/pydantic-ai.git",
            str(target),
        ]
    ]
    assert not (target / "partial.txt").exists()
    assert (target / ".git").exists()


def test_refresh_clone_skips_existing_valid_cached_repo(tmp_path, monkeypatch):
    target = tmp_path / "pydantic-pydantic-ai"
    target.mkdir()
    (target / ".git").mkdir()

    calls = []
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: calls.append(args))

    refresh_clone("https://github.com/pydantic/pydantic-ai.git", target)

    assert calls == []


def test_collect_code_matches_searches_existing_clone(tmp_path):
    source = tmp_path / "src" / "agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("logfire.instrument_pydantic_ai()\n", encoding="utf-8")

    matches = collect_code_matches("pydantic/pydantic-ai", tmp_path, ["logfire"])

    assert len(matches) == 1
    assert matches[0][1].path == "src/agent.py"


def test_split_evidence_refs_separates_code_and_non_code():
    evidence = [
        EvidenceResult(
            tool=ToolName.CODE_SEARCH,
            query="routing",
            summary="found route definition",
            items=[
                EvidenceItem(
                    kind="code",
                    title="routing",
                    url="https://github.com/fastapi/fastapi/blob/HEAD/fastapi/routing.py#L35",
                    excerpt="...",
                )
            ],
        ),
        EvidenceResult(
            tool=ToolName.README,
            query="routing",
            summary="README summary",
            items=[
                EvidenceItem(
                    kind="readme",
                    title="readme",
                    url="https://github.com/fastapi/fastapi#readme",
                    excerpt="...",
                )
            ],
        ),
        EvidenceResult(
            tool=ToolName.CODE_SEARCH,
            query="routing",
            summary="second code file",
            items=[
                EvidenceItem(
                    kind="code",
                    title="applications",
                    url="https://github.com/fastapi/fastapi/blob/HEAD/fastapi/applications.py#L6",
                    excerpt="...",
                )
            ],
        ),
    ]

    non_code_refs, code_refs = split_evidence_refs(evidence)

    assert non_code_refs == ["https://github.com/fastapi/fastapi#readme"]
    assert code_refs == [
        "https://github.com/fastapi/fastapi/blob/HEAD/fastapi/routing.py#L35",
        "https://github.com/fastapi/fastapi/blob/HEAD/fastapi/applications.py#L6",
    ]


def test_compact_code_refs_deduplicates_by_file():
    assert compact_code_refs(
        [
            "https://github.com/fastapi/fastapi/blob/HEAD/fastapi/routing.py#L35",
            "https://github.com/fastapi/fastapi/blob/HEAD/fastapi/routing.py#L80",
            "https://github.com/fastapi/fastapi/blob/HEAD/fastapi/applications.py#L6",
        ]
    ) == [
        "fastapi/routing.py:35",
        "fastapi/applications.py:6",
    ]


def test_compact_code_refs_supports_root_level_file_and_ranges():
    assert compact_code_refs(
        [
            "https://github.com/fastapi/fastapi/blob/HEAD/README.md#L12",
            "https://github.com/fastapi/fastapi/blob/HEAD/src/main.py#L25-L28",
        ]
    ) == [
        "README.md:12",
        "src/main.py:25",
    ]


def test_sync_clone_to_sandbox_copies_repo_minus_git(tmp_path):
    cache = tmp_path / "cache" / "pydantic-pydantic-ai"
    source_file = cache / "src" / "agent.py"
    git_file = cache / ".git" / "config"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    git_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("x", encoding="utf-8")
    git_file.write_text("ignore", encoding="utf-8")

    sandbox = tmp_path / "sandbox" / "session-1" / "repos" / "pydantic-pydantic-ai"
    sync_clone_to_sandbox(cache, sandbox)

    assert (sandbox / "src" / "agent.py").exists()
    assert not (sandbox / ".git").exists()


def test_clone_lock_returns_same_lock_for_same_target(tmp_path):
    target = tmp_path / "repo"

    assert clone_lock(target) is clone_lock(target)


def test_github_evidence_tools_uses_injected_token():
    tools = GitHubEvidenceTools(github_token="token")

    assert tools._headers()["Authorization"] == "Bearer token"
