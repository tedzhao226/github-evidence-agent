from pathlib import Path

from cloudbees_agent.tools import (
    clone_lock,
    clone_target_path,
    collect_code_matches,
    default_clone_root,
    extract_terms,
    iter_source_files,
    refresh_clone,
)


def test_extract_terms_adds_observability_synonyms():
    terms = extract_terms("How does this repo support observability or tracing?")

    assert "observability" in terms
    assert "logfire" in terms
    assert "trace" in terms


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


def test_collect_code_matches_searches_existing_clone(tmp_path):
    source = tmp_path / "src" / "agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("logfire.instrument_pydantic_ai()\n", encoding="utf-8")

    matches = collect_code_matches("pydantic/pydantic-ai", tmp_path, ["logfire"])

    assert len(matches) == 1
    assert matches[0][1].path == "src/agent.py"


def test_clone_lock_returns_same_lock_for_same_target(tmp_path):
    target = tmp_path / "repo"

    assert clone_lock(target) is clone_lock(target)
