# paper-boot

Automates the setup of academic ML repositories.

## Usage

```bash
paper-boot <github_url>
paper-boot <arxiv_id_or_url>
```

**From a GitHub URL (existing):**
```bash
paper-boot https://github.com/neuraloperator/neuraloperator
```

**From an arXiv paper (new):**
```bash
paper-boot 2305.12345
paper-boot https://arxiv.org/abs/2305.12345
```

When given an arXiv ID or URL, paper-boot will:
1. Fetch paper metadata from the arXiv API
2. Search Papers with Code and the arXiv page for the linked GitHub repo
3. Let you choose if multiple repos are found
4. Clone the repo and scaffold `run_baseline.sh`

When given a GitHub URL directly, it will:
1. Clone the repository into the current directory
2. Scan for dependency files (`requirements.txt`, `environment.yml`, `setup.py`, `pyproject.toml`)
3. Generate a `run_baseline.sh` script with conda env creation + dependency install
4. Print a summary of what was found and what to do next

## Requirements

```bash
pip install click
```

## Extending

To add new dependency heuristics, edit `DEPENDENCY_FILES` in `paper_boot.py`:

```python
DEPENDENCY_FILES = {
    "environment.yml": "conda",
    "requirements.txt": "pip",
    # add new entries here
}
```
