from collections.abc import Iterable
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Lock
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from cloudbees_agent.models import EvidenceItem, EvidenceResult, ToolName


CLONE_LOCKS: dict[Path, Lock] = {}
CLONE_LOCKS_GUARD = Lock()


class GitHubEvidenceTools:
    """Read-only GitHub evidence tools used by the agent runner."""

    api_base = "https://api.github.com"

    def __init__(self, clone_root: Path | None = None) -> None:
        """Accept an optional clone root for bounded code search."""
        self.clone_root = clone_root or default_clone_root()

    def run(self, tool: ToolName, repo: str, question: str) -> EvidenceResult:
        """Dispatch the selected tool name to its concrete evidence lookup."""
        if tool == ToolName.README:
            return self.readme(repo, question)
        if tool == ToolName.ISSUES:
            return self.issues(repo, question)
        if tool == ToolName.COMMITS:
            return self.commits(repo, question)
        if tool == ToolName.CODE_SEARCH:
            return self.code_search(repo, question)
        raise ValueError(f"Unknown tool: {tool}")

    def readme(self, repo: str, question: str) -> EvidenceResult:
        """Fetch the repository README and return lines matching question terms."""
        payload = self._get_json(f"/repos/{repo}/readme")
        download_url = payload.get("download_url")
        text = self._get_text(download_url) if download_url else ""
        terms = extract_terms(question)
        excerpts = relevant_lines(text.splitlines(), terms, limit=6)
        items = [
            EvidenceItem(
                kind="readme",
                title="README",
                url=f"https://github.com/{repo}#readme",
                excerpt=excerpt,
            )
            for excerpt in excerpts
        ]
        return EvidenceResult(
            tool=ToolName.README,
            query=" ".join(terms),
            summary=summarize_items(items),
            items=items,
        )

    def issues(self, repo: str, question: str) -> EvidenceResult:
        """Search public GitHub issues for question terms scoped to one repo."""
        terms = extract_terms(question)
        query = f"repo:{repo} is:issue " + " ".join(terms[:4])
        payload = self._get_json(f"/search/issues?{urlencode({'q': query, 'per_page': '5'})}")
        items = [
            EvidenceItem(
                kind="issue",
                title=item.get("title", "Issue"),
                url=item.get("html_url"),
                excerpt=(item.get("body") or item.get("title") or "")[:500],
            )
            for item in payload.get("items", [])
        ]
        return EvidenceResult(
            tool=ToolName.ISSUES,
            query=query,
            summary=summarize_items(items),
            items=items,
        )

    def commits(self, repo: str, question: str) -> EvidenceResult:
        """Fetch recent commits and keep messages that match question terms."""
        payload = self._get_json(f"/repos/{repo}/commits?per_page=10")
        terms = extract_terms(question)
        items = []
        for item in payload:
            commit = item.get("commit", {})
            message = commit.get("message", "")
            if terms and not contains_any(message, terms):
                continue
            items.append(
                EvidenceItem(
                    kind="commit",
                    title=message.splitlines()[0] if message else "Commit",
                    url=item.get("html_url"),
                    excerpt=message[:500],
                )
            )
        return EvidenceResult(
            tool=ToolName.COMMITS,
            query=" ".join(terms),
            summary=summarize_items(items),
            items=items[:5],
        )

    def code_search(self, repo: str, question: str) -> EvidenceResult:
        """Clone the repo shallowly, search bounded source files, and rank matches."""
        terms = extract_terms(question)
        clone_url = f"https://github.com/{repo}.git"
        target = clone_target_path(repo, self.clone_root)
        with clone_lock(target):
            refresh_clone(clone_url, target)
            candidates = collect_code_matches(repo, target, terms)
        items = [
            item
            for _, item in sorted(
                candidates,
                key=lambda candidate: (
                    candidate[0],
                    "package-lock" not in (candidate[1].path or ""),
                    candidate[1].path or "",
                ),
                reverse=True,
            )[:8]
        ]
        return EvidenceResult(
            tool=ToolName.CODE_SEARCH,
            query=" ".join(terms),
            summary=summarize_items(items),
            items=items,
        )

    def _get_json(self, path: str) -> object:
        """Read JSON from GitHub REST endpoints with optional token auth."""
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        request = Request(url, headers=self._headers())
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get_text(self, url: str) -> str:
        """Read raw text from a GitHub-provided URL."""
        request = Request(url, headers=self._headers())
        with urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore")

    def _headers(self) -> dict[str, str]:
        """Build GitHub request headers, adding GITHUB_TOKEN when available."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "cloudbees-agent-assessment",
        }
        if token := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
        return headers


def score_match(path: str, line: str, terms: list[str]) -> int:
    """Rank code-search hits so source and tracing-specific lines appear first."""
    lowered = f"{path}\n{line}".lower()
    score = sum(1 for term in terms if term.lower() in lowered)
    if "logfire" in lowered and ("trace" in lowered or "tracing" in lowered):
        score += 3
    if "instrument" in lowered:
        score += 2
    if path.endswith(".py"):
        score += 1
    return score


def default_clone_root() -> Path:
    """Return the repo-local default root for temporary repository clones."""
    return Path.cwd() / "tmp" / "repos"


def clone_target_path(repo: str, clone_root: Path) -> Path:
    """Map owner/name to a repo-specific clone directory under clone_root."""
    return clone_root / repo.replace("/", "-")


def clone_lock(target: Path) -> Lock:
    """Return a per-target lock so local clone refresh and search are serialized."""
    key = target.resolve()
    with CLONE_LOCKS_GUARD:
        if key not in CLONE_LOCKS:
            CLONE_LOCKS[key] = Lock()
        return CLONE_LOCKS[key]


def refresh_clone(clone_url: str, target: Path) -> None:
    """Create a fresh shallow clone, tolerating stale partial clone directories."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", clone_url, str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        raise RuntimeError(f"git clone failed for {clone_url}: {stderr}") from exc


def collect_code_matches(repo: str, target: Path, terms: list[str]) -> list[tuple[int, EvidenceItem]]:
    """Search one cloned repository and return scored evidence candidates."""
    candidates: list[tuple[int, EvidenceItem]] = []
    for path in iter_source_files(target):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if contains_any(line, terms):
                rel = path.relative_to(target)
                score = score_match(str(rel), line, terms)
                candidates.append(
                    (
                        score,
                        EvidenceItem(
                            kind="code",
                            title=f"{rel}:{line_number}",
                            url=f"https://github.com/{repo}/blob/HEAD/{quote(str(rel))}#L{line_number}",
                            path=str(rel),
                            excerpt=line.strip()[:500],
                        ),
                    )
                )
                break
    return candidates


def extract_terms(question: str) -> list[str]:
    """Convert a natural-language question into simple evidence search terms."""
    words = [word.strip(".,?!:;()[]{}\"'").lower() for word in question.split()]
    stop = {
        "the",
        "and",
        "or",
        "does",
        "this",
        "that",
        "how",
        "where",
        "what",
        "repository",
        "repo",
        "support",
    }
    terms = [word for word in words if len(word) > 2 and word not in stop]
    if "observability" in question.lower() and "logfire" not in terms:
        terms.append("logfire")
    if "tracing" in question.lower() and "trace" not in terms:
        terms.append("trace")
    return terms[:8] or ["readme"]


def relevant_lines(lines: Iterable[str], terms: list[str], limit: int) -> list[str]:
    """Return the first non-empty lines containing any search term."""
    matches = [line.strip() for line in lines if line.strip() and contains_any(line, terms)]
    return matches[:limit]


def contains_any(text: str, terms: list[str]) -> bool:
    """Check whether text contains at least one case-insensitive term."""
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def summarize_items(items: list[EvidenceItem]) -> str:
    """Produce a compact trace summary from the first few evidence excerpts."""
    if not items:
        return ""
    return " | ".join(item.excerpt for item in items[:3])


def iter_source_files(root: Path) -> Iterable[Path]:
    """Yield searchable source/doc files while skipping generated or vendor paths."""
    ignored = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "cassettes",
        "docs-site",
        "tests",
        "vendor",
        "__pycache__",
    }
    suffixes = {
        ".py",
        ".md",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
    }
    for path in root.rglob("*"):
        if any(part in ignored for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in suffixes:
            yield path
