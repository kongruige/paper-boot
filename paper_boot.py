#!/usr/bin/env python3
"""paper-boot: automate the setup of academic ML repositories."""

import re
import subprocess
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
@click.argument("github_url")
def main(github_url: str) -> None:
    """Clone a GitHub repo and scaffold a run_baseline.sh setup script."""
    click.echo(click.style(f"Cloning {github_url} …", fg="blue"))

    repo_path = clone_repo(github_url)
    repo_name = repo_path.name

    deps = scan_dependencies(repo_path)
    custom_scripts = scan_custom_scripts(repo_path)
    script_path = generate_run_script(repo_path, repo_name, deps)
    print_summary(repo_path, repo_name, deps, script_path, custom_scripts)


if __name__ == "__main__":
    main()
