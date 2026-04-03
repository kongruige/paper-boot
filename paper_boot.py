#!/usr/bin/env python3
"""paper-boot: automate the setup of academic ML repositories."""

import json
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# Heuristics: add new entries here to extend dependency detection
# ---------------------------------------------------------------------------
DEPENDENCY_FILES = {
    "environment.yml": "conda",
    "environment.yaml": "conda",
    "requirements.txt": "pip",
    "setup.py": "pip-setup",
    "pyproject.toml": "pip-pyproject",
}

TORCH_PKG_NAMES = {"torch", "torchvision", "torchaudio", "pytorch"}

CUSTOM_INSTALL_SCRIPTS = ["setup.sh", "install.sh"]

MODERN_PYTORCH_INSTALL = (
    "conda install pytorch torchvision torchaudio pytorch-cuda=11.8 "
    "-c pytorch -c nvidia -y"
)

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
GITHUB_REPO_RE = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+")

# Paths on github.com that are not repositories
_GITHUB_NON_REPO = {"topics", "features", "settings", "explore", "marketplace",
                    "notifications", "login", "signup", "pricing", "about"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pkg_name(line: str) -> str:
    """Extract the bare package name from a requirements.txt or conda dep line."""
    stripped = line.split("#")[0].strip().lstrip("- ")
    return re.split(r"[>=<!~\[\s]", stripped)[0].lower()


def _is_torch_line(line: str) -> bool:
    """Return True if a dep line installs a PyTorch package."""
    stripped = line.split("#")[0].strip()
    return bool(stripped) and _pkg_name(stripped) in TORCH_PKG_NAMES


def _has_torch_in_conda_env(dep_path: Path) -> bool:
    """Return True if an environment.yml contains any PyTorch packages."""
    for line in dep_path.read_text(errors="replace").splitlines():
        if _is_torch_line(line):
            return True
    return False


# ---------------------------------------------------------------------------
# arXiv → GitHub repo resolution
# ---------------------------------------------------------------------------
def _is_arxiv_input(source: str) -> bool:
    """Return True if the source looks like an arXiv ID or URL rather than a GitHub URL."""
    return bool(ARXIV_ID_RE.search(source)) and "github.com" not in source


def parse_arxiv_id(source: str) -> str:
    """Extract a bare arXiv ID (e.g. '2305.12345') from a URL or raw ID."""
    m = ARXIV_ID_RE.search(source)
    if m:
        return m.group(1)
    raise click.ClickException(f"Could not parse arXiv ID from: {source}")


def _normalize_github_url(url: str) -> str | None:
    """Normalize a GitHub URL to https://github.com/owner/repo, or None if invalid."""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # Strip fragments, query strings, and deep paths beyond owner/repo
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if owner.lower() in _GITHUB_NON_REPO:
        return None
    return f"https://github.com/{owner}/{repo}"


def _urlopen_safe(url: str, *, timeout: int = 15, headers: dict | None = None) -> bytes:
    """Fetch a URL, returning bytes. Raises click.ClickException on failure."""
    hdrs = {"User-Agent": "paper-boot/0.1"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        raise click.ClickException(f"HTTP request failed ({url}): {exc}")


def fetch_arxiv_metadata(arxiv_id: str) -> dict:
    """Fetch title, abstract, and any GitHub links from the arXiv API."""
    api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    xml_bytes = _urlopen_safe(api_url)
    ns = {"atom": "http://www.w3.org/2005/Atom",
          "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml_bytes)
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise click.ClickException(f"No arXiv entry found for {arxiv_id}")

    title = " ".join((entry.findtext("atom:title", "", ns)).split())
    abstract = entry.findtext("atom:summary", "", ns).strip()

    # Collect GitHub URLs from <link> elements and the arxiv:comment field
    github_urls: list[str] = []
    for link in entry.findall("atom:link", ns):
        href = link.get("href", "")
        if "github.com" in href:
            github_urls.append(href)
    comment = entry.findtext("arxiv:comment", "", ns) or ""
    github_urls.extend(GITHUB_REPO_RE.findall(comment))
    # Also scan the abstract itself (some authors embed repo links)
    github_urls.extend(GITHUB_REPO_RE.findall(abstract))

    return {"title": title, "abstract": abstract,
            "github_links": github_urls, "arxiv_id": arxiv_id}


def search_paperswithcode(arxiv_id: str) -> list[str]:
    """Query Papers with Code for repos linked to this arXiv paper."""
    try:
        data = json.loads(_urlopen_safe(
            f"https://paperswithcode.com/api/v1/papers/?arxiv_id={arxiv_id}",
            headers={"Accept": "application/json"},
        ))
        results = data.get("results", [])
        if not results:
            return []
        paper_id = results[0].get("id")
        if not paper_id:
            return []
        repos_data = json.loads(_urlopen_safe(
            f"https://paperswithcode.com/api/v1/papers/{paper_id}/repositories/",
            headers={"Accept": "application/json"},
        ))
        repos = repos_data.get("results", [])
        # Official repos first, then by stars descending
        repos.sort(key=lambda r: (not r.get("is_official", False),
                                  -(r.get("stars", 0) or 0)))
        return [r["url"] for r in repos if r.get("url")]
    except click.ClickException:
        return []


def search_arxiv_page_for_github(arxiv_id: str) -> list[str]:
    """Scrape the arXiv abstract page HTML for GitHub links."""
    try:
        html = _urlopen_safe(f"https://arxiv.org/abs/{arxiv_id}").decode(errors="replace")
        return GITHUB_REPO_RE.findall(html)
    except click.ClickException:
        return []


def find_repo_for_paper(arxiv_id: str) -> str:
    """Resolve an arXiv ID to a GitHub repo URL. Prompts user if ambiguous."""
    cyan = lambda s: click.style(s, fg="cyan")

    click.echo(click.style("Fetching paper metadata from arXiv …", fg="blue"))
    meta = fetch_arxiv_metadata(arxiv_id)
    click.echo(f"  Paper: {cyan(meta['title'])}")
    click.echo()

    candidates: list[str] = []

    # 1. Direct links from arXiv metadata
    candidates.extend(meta["github_links"])

    # 2. Papers with Code
    click.echo(click.style("Searching Papers with Code …", fg="blue"))
    candidates.extend(search_paperswithcode(arxiv_id))

    # 3. Scrape arXiv abstract page
    if not candidates:
        click.echo(click.style("Searching arXiv page for GitHub links …", fg="blue"))
        candidates.extend(search_arxiv_page_for_github(arxiv_id))

    # Normalize and deduplicate
    seen: set[str] = set()
    unique: list[str] = []
    for raw in candidates:
        norm = _normalize_github_url(raw)
        if norm and norm not in seen:
            seen.add(norm)
            unique.append(norm)

    if not unique:
        raise click.ClickException(
            f"No GitHub repository found for arXiv:{arxiv_id}.\n"
            f"  Paper: {meta['title']}\n"
            f"  Try searching manually and use:  paper-boot <github_url>"
        )

    if len(unique) == 1:
        click.echo(click.style("Found repository: ", fg="green", bold=True) + unique[0])
        return unique[0]

    # Multiple candidates — let user pick
    click.echo(click.style("Multiple repositories found:", fg="yellow", bold=True))
    for i, url in enumerate(unique, 1):
        click.echo(f"  [{i}] {url}")
    choice = click.prompt("Select repository", type=click.IntRange(1, len(unique)), default=1)
    return unique[choice - 1]


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------
def clone_repo(github_url: str) -> Path:
    """Clone the repository into cwd. Returns the path to the cloned directory."""
    repo_name = github_url.rstrip("/").split("/")[-1].removesuffix(".git")
    result = subprocess.run(
        ["git", "clone", github_url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(f"git clone failed:\n{result.stderr.strip()}")
    repo_path = Path.cwd() / repo_name
    if not repo_path.exists():
        raise click.ClickException(f"Cloned directory not found at {repo_path}")
    return repo_path


def scan_dependencies(repo_path: Path) -> dict[str, Path]:
    """Scan the repo root for known dependency files. Returns {label: path}."""
    found = {}
    for filename, label in DEPENDENCY_FILES.items():
        candidate = repo_path / filename
        if candidate.exists():
            found[label] = candidate
    return found


def scan_custom_scripts(repo_path: Path) -> list[Path]:
    """Return paths of author-provided install scripts (setup.sh, install.sh)."""
    return [repo_path / s for s in CUSTOM_INSTALL_SCRIPTS if (repo_path / s).exists()]


def generate_run_script(repo_path: Path, repo_name: str, deps: dict[str, Path]) -> Path:
    """Write run_baseline.sh into repo root and return its path."""
    lines = [
        "#!/bin/bash",
        "set -e",
        "",
        f"# --- Environment setup for: {repo_name} ---",
        f'conda create -n "{repo_name}" python=3.10 -y',
        f'conda activate "{repo_name}"',
        "",
        "# --- Install dependencies ---",
    ]

    torch_stripped = False  # track whether we need the pytorch TODO block

    # Priority: conda env file > requirements.txt > setup.py/pyproject.toml
    if "conda" in deps:
        dep_path = deps["conda"]
        if _has_torch_in_conda_env(dep_path):
            torch_stripped = True
            lines += [
                f"# !! paper-boot: PyTorch packages detected in {dep_path.name}.",
                f"# !! These are likely version-pinned and may conflict with cluster CUDA drivers.",
                f"# !! Remove torch/pytorch/torchvision/torchaudio from {dep_path.name} before running,",
                f"# !! then install a cluster-compatible build via the block below.",
                f"conda env update -n \"{repo_name}\" -f {dep_path.name} --prune",
            ]
        else:
            lines.append(f"conda env update -n \"{repo_name}\" -f {dep_path.name} --prune")

    if "pip" in deps:
        req_path = deps["pip"]
        req_lines = req_path.read_text(errors="replace").splitlines()
        lines.append(f"# Expanded from {req_path.name} (PyTorch lines commented out by paper-boot)")
        for req_line in req_lines:
            raw = req_line.strip()
            if not raw or raw.startswith("#"):
                continue
            if _is_torch_line(raw):
                torch_stripped = True
                lines.append(
                    f"# pip install {raw}"
                    "  # <-- paper-boot: version-pinned PyTorch, likely broken on modern CUDA"
                )
            else:
                lines.append(f"pip install {raw}")

    if "pip-setup" in deps or "pip-pyproject" in deps:
        lines.append("pip install -e .")

    if not deps:
        lines.append("# No dependency files detected — install manually")

    # PyTorch replacement block
    if torch_stripped:
        lines += [
            "",
            "# --- PyTorch (cluster-compatible) ---",
            "# TODO: INSTALL UW HYAK CLUSTER PYTORCH HERE",
            "# Uncomment and adjust the line below for your cluster's CUDA version:",
            f"# {MODERN_PYTORCH_INSTALL}",
        ]

    lines += [
        "",
        "# --- Run ---",
        "# TODO: paste your training / evaluation command below",
        "# e.g.: python train.py --config configs/default.yaml",
        "",
    ]

    script_path = repo_path / "run_baseline.sh"
    script_path.write_text("\n".join(lines) + "\n")
    script_path.chmod(0o755)
    return script_path


def print_summary(
    repo_path: Path,
    repo_name: str,
    deps: dict[str, Path],
    script_path: Path,
    custom_scripts: list[Path],
) -> None:
    """Print a colored summary of what was found and what to do next."""
    green  = lambda s: click.style(s, fg="green",  bold=True)
    yellow = lambda s: click.style(s, fg="yellow", bold=True)
    red    = lambda s: click.style(s, fg="red",    bold=True)
    cyan   = lambda s: click.style(s, fg="cyan")

    click.echo()
    click.echo(green("✓ Repository cloned") + f"  →  {repo_path}")
    click.echo()

    if deps:
        click.echo(yellow("Detected dependency files:"))
        for label, path in deps.items():
            click.echo(f"  [{label}]  {path.name}")
    else:
        click.echo(yellow("No dependency files detected.") + " You'll need to install manually.")

    if custom_scripts:
        click.echo()
        for s in custom_scripts:
            click.echo(
                red("⚠ Custom install script found: ") + str(s.name) +
                "\n  The authors provided their own setup — review it before running run_baseline.sh."
            )

    click.echo()
    click.echo(green("✓ Generated:") + f"  {script_path}")
    click.echo()
    click.echo(yellow("Next steps:"))
    click.echo(cyan(f"  cd {repo_path}"))
    click.echo(cyan( "  # Edit run_baseline.sh — fill in the TODO pytorch + run commands"))
    click.echo(cyan( "  bash run_baseline.sh"))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
@click.command()
@click.argument("source")
def main(source: str) -> None:
    """Clone a GitHub repo (or find one from an arXiv paper) and scaffold run_baseline.sh.

    SOURCE can be a GitHub URL, an arXiv ID (e.g. 2305.12345), or an arXiv URL.
    """
    if _is_arxiv_input(source):
        arxiv_id = parse_arxiv_id(source)
        github_url = find_repo_for_paper(arxiv_id)
    else:
        github_url = source

    click.echo(click.style(f"Cloning {github_url} …", fg="blue"))

    repo_path = clone_repo(github_url)
    repo_name = repo_path.name

    deps = scan_dependencies(repo_path)
    custom_scripts = scan_custom_scripts(repo_path)
    script_path = generate_run_script(repo_path, repo_name, deps)
    print_summary(repo_path, repo_name, deps, script_path, custom_scripts)


if __name__ == "__main__":
    main()
