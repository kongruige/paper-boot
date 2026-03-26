# paper-boot

Automates the setup of academic ML repositories.

## Usage

```bash
python paper_boot.py <github_url>
```

**Example:**
```bash
python paper_boot.py https://github.com/neuraloperator/neuraloperator
```

This will:
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
