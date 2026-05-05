import re
from urllib.parse import urlparse


GITHUB_URL_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
OWNER_NAME_RE = re.compile(r"(?<![\w.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w.-])")


def parse_repo(value: str) -> tuple[str, str]:
    """Parse owner/name or a GitHub URL into repository owner and name."""
    candidate = value.strip().removesuffix("/")
    if candidate.startswith("http://") or candidate.startswith("https://"):
        parsed = urlparse(candidate)
        if parsed.netloc != "github.com":
            raise ValueError("Repository URL must point to github.com")
        parts = [part for part in parsed.path.split("/") if part]
    else:
        parts = [part for part in candidate.split("/") if part]

    if len(parts) != 2:
        raise ValueError("Repository must be owner/name or https://github.com/owner/name")
    owner, name = parts
    if not owner or not name:
        raise ValueError("Repository owner and name are required")
    return owner, name.removesuffix(".git")


def normalize_repo(value: str) -> str:
    """Return the canonical owner/name repository string used by tools."""
    owner, name = parse_repo(value)
    return f"{owner}/{name}"


def find_repo(text: str) -> str | None:
    """Return the first GitHub repository mentioned in free-form text."""
    if match := GITHUB_URL_RE.search(text):
        name = match.group(2).rstrip(".,:;!?)]}")
        return normalize_repo(f"{match.group(1)}/{name}")
    if match := OWNER_NAME_RE.search(text):
        try:
            return normalize_repo(match.group(1).rstrip(".,:;!?)]}"))
        except ValueError:
            return None
    return None
