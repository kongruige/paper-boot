#!/usr/bin/env python3
"""paper-boot: automate the setup of academic ML repositories."""

import subprocess
import sys
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

    # Priority: conda env file > requirements.txt > setup.py/pyproject.toml
    if "conda" in deps:
        dep_path = deps["conda"]
        lines.append(f"conda env update -n \"{repo_name}\" -f {dep_path.name} --prune")
    if "pip" in deps:
        lines.append(f"pip install -r {deps['pip'].name}")
    if "pip-setup" in deps:
        lines.append("pip install -e .")
    if "pip-pyproject" in deps:
        lines.append("pip install -e .")
    if not deps:
        lines.append("# No dependency files detected — install manually")

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


def print_summary(repo_path: Path, repo_name: str, deps: dict[str, Path], script_path: Path) -> None:
    """Print a colored summary of what was found and what to do next."""
    green = lambda s: click.style(s, fg="green", bold=True)
    yellow = lambda s: click.style(s, fg="yellow", bold=True)
    cyan = lambda s: click.style(s, fg="cyan")

    click.echo()
    click.echo(green("✓ Repository cloned") + f"  →  {repo_path}")
    click.echo()

    if deps:
        click.echo(yellow("Detected dependency files:"))
        for label, path in deps.items():
            click.echo(f"  [{label}]  {path.name}")
    else:
        click.echo(yellow("No dependency files detected.") + " You'll need to install manually.")

    click.echo()
    click.echo(green("✓ Generated:") + f"  {script_path}")
    click.echo()
    click.echo(yellow("Next steps:"))
    click.echo(cyan(f"  cd {repo_path}"))
    click.echo(cyan( "  # Edit run_baseline.sh — fill in the TODO command"))
    click.echo(cyan( "  bash run_baseline.sh"))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
@click.command()
@click.argument("github_url")
def main(github_url: str) -> None:
    """Clone a GitHub repo and scaffold a run_baseline.sh setup script."""
    click.echo(click.style(f"Cloning {github_url} …", fg="blue"))

    repo_path = clone_repo(github_url)
    repo_name = repo_path.name

    deps = scan_dependencies(repo_path)
    script_path = generate_run_script(repo_path, repo_name, deps)
    print_summary(repo_path, repo_name, deps, script_path)


if __name__ == "__main__":
    main()
