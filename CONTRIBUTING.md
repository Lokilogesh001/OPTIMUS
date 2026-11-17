# Contributing to OPTIMUS

Thank you for your interest in contributing to **OPTIMUS** — the autonomous personal agentic AI assistant.

---

## Code of Conduct & Core Principles

1. **Non-Commercial Research & Development**: OPTIMUS is distributed under Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0).
2. **Security First**: Never commit API keys, personal credentials, live tokens, or unredacted conversation logs.
3. **Rigorous Verification**: Code changes must include test coverage or pass existing verification suites (`pytest`, `ruff`).

---

## Development Environment Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Lokilogesh001/OPTIMUS.git
   cd OPTIMUS
   ```

2. **Set Up Python Virtual Environment**:
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .\.venv\Scripts\Activate.ps1
   # Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment**:
   ```bash
   cp .env.example .env
   cp config/api_keys.json.example config/api_keys.json
   ```
   Add your local or cloud keys as needed.

---

## Code Quality Standards

Before submitting changes, run linting and unit tests:

```bash
# Linting & Style Check
ruff check .

# Fast Syntax & Import Validation
ruff check --select E,F .

# Run Unit Test Suites
pytest tests/
```

### Key Guidelines
- **Modularity**: Specialized agent logic belongs in `core/agents/`, workflow definitions in `core/workflows/`, and UI widgets in `gui/`.
- **Deterministic Verification**: Tasks that modify state or generate files must emit cryptographic evidence via `core/verification/`.
- **Error Resilience**: Background loops must catch recoverable exceptions without crashing the primary Qt/Live event loops.

---

## Submitting Pull Requests

1. Fork the repository and create a feature branch (`git checkout -b feat/my-improvement`).
2. Commit with conventional commit messages (`feat: ...`, `fix: ...`, `docs: ...`, `refactor: ...`).
3. Ensure no local secrets or database files are staged (`git status`).
4. Push to your branch and open a Pull Request against `main`.
