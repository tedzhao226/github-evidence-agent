from collections.abc import Iterable
import json
from pathlib import Path
import shutil
import subprocess
from threading import Lock
from urllib.parse import quote, urlencode, unquote, urlparse
from urllib.request import Request, urlopen

from cloudbees_agent.models import EvidenceItem, EvidenceResult, ToolName


CLONE_LOCKS: dict[Path, Lock] = {}
CLONE_LOCKS_GUARD = Lock()


class GitHubEvidenceTools:
    """Read-only GitHub evidence tools used by the agent runner."""

    api_base = "https://api.github.com"

    def __init__(
        self,
        clone_root: Path | None = None,
        sandbox_root: Path | None = None,
        github_token: str | None = None,
    ) -> None:
        """Accept optional cache and sandbox roots for code search."""
        self.clone_root = clone_root or default_clone_root()
        self.sandbox_root = sandbox_root or default_sandbox_root()
        self.github_token = github_token

    def readme(self, repo: str, query: str) -> EvidenceResult:
        """Fetch the repository README and return lines matching query terms."""
        payload = self._get_json(f"/repos/{repo}/readme")
        download_url = payload.get("download_url")
        text = self._get_text(download_url) if download_url else ""
        terms = query_terms(query)
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

    def issues(self, repo: str, query: str) -> EvidenceResult:
        """Search public GitHub issues for query terms scoped to one repo."""
        terms = query_terms(query)
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

    def commits(self, repo: str, query: str) -> EvidenceResult:
        """Fetch recent commits and keep messages that match query terms."""
        payload = self._get_json(f"/repos/{repo}/commits?per_page=10")
        terms = query_terms(query)
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

    def code_search(self, repo: str, query: str) -> EvidenceResult:
        """Use cached clones and copy into a session sandbox before searching."""
        terms = query_terms(query)
        clone_url = f"https://github.com/{repo}.git"
        cache_target = clone_target_path(repo, self.clone_root)
        sandbox_target = clone_target_path(repo, self.sandbox_root)
        with clone_lock(cache_target):
            refresh_clone(clone_url, cache_target)
            sync_clone_to_sandbox(cache_target, sandbox_target)
            candidates = collect_code_matches(repo, sandbox_target, terms)
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
        """Build GitHub request headers, adding auth when available."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "cloudbees-agent-assessment",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
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


def split_evidence_refs(
    evidence: list[EvidenceResult],
) -> tuple[list[str], list[str]]:
    """Split references into non-code evidence refs and code search refs."""
    non_code_refs: list[str] = []
    code_refs: list[str] = []
    seen_non_code: set[str] = set()
    seen_code: set[str] = set()

    for result in evidence:
        for item in result.items:
            ref = item.url or item.path or item.title
            if not ref:
                continue
            dedupe_key = _dedupe_key(ref)
            if result.tool == ToolName.CODE_SEARCH:
                if dedupe_key in seen_code:
                    continue
                seen_code.add(dedupe_key)
                code_refs.append(ref)
                continue
            if dedupe_key in seen_non_code:
                continue
            seen_non_code.add(dedupe_key)
            non_code_refs.append(ref)

    return non_code_refs, code_refs


def compact_code_refs(refs: list[str]) -> list[str]:
    """Convert blob URLs to file:line refs and de-duplicate by file path."""
    output: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        parsed = _github_file_line_ref(ref)
        if parsed is None:
            if ref in seen:
                continue
            output.append(ref)
            seen.add(ref)
            continue
        path, line = parsed
        if path in seen:
            continue
        output.append(f"{path}:{line}")
        seen.add(path)
    return output


def _github_file_line_ref(ref: str) -> tuple[str, str] | None:
    """Return repository-relative file path and line from a GitHub blob URL."""
    parsed = urlparse(ref)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != "github.com":
        return None
    parts = parsed.path.split("/")
    if len(parts) < 6 or parts[3] != "blob" or parts[4] != "HEAD":
        return None
    if not parsed.fragment.startswith("L"):
        return None
    fragment = parsed.fragment
    if fragment.startswith("L") and "-" in fragment:
        line = fragment[1:].split("-", 1)[0]
    else:
        line = parsed.fragment[1:]
    if not line.isdigit():
        return None
    rel_path = "/".join(parts[5:])
    if not rel_path:
        return None
    return unquote(rel_path), line


def _dedupe_key(ref: str) -> str:
    key = _github_file_line_ref(ref)
    if key:
        return key[0]
    return ref


def default_clone_root() -> Path:
    """Return the repo-local default root for temporary repository clones."""
    return Path.cwd() / "tmp" / "repos"


def default_sandbox_root() -> Path:
    """Return the repo-local default session sandbox root."""
    return Path.cwd() / "tmp" / "sandbox" / "sessions"


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
    """Create a shallow clone when the cached repository is missing."""
    if target.exists():
        if (target / ".git").is_dir():
            return
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


def sync_clone_to_sandbox(cache_path: Path, sandbox_path: Path) -> None:
    """Synchronize a cached clone into a per-session sandbox directory."""
    sandbox_path.parent.mkdir(parents=True, exist_ok=True)
    if sandbox_path.exists():
        shutil.rmtree(sandbox_path, ignore_errors=True)
    shutil.copytree(cache_path, sandbox_path, ignore=shutil.ignore_patterns(".git"))


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


def query_terms(query: str) -> list[str]:
    """Convert a model-written query into simple evidence search terms."""
    words = [word.strip(".,?!:;()[]{}\"'").lower() for word in query.split()]
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
    return terms[:8]


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
