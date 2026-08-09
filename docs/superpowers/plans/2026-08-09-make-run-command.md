# Make Run Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide `make run` as the documented command for opening the Desktop app from the repository root.

**Architecture:** A root `Makefile` wraps the existing Python module command without changing packaging or runtime code. `README.md` is the user-facing source of the command after environment setup.

**Tech Stack:** GNU Make, Python virtual environment, Markdown.

## Global Constraints

- `run` must execute `PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main`.
- Add no other Make targets, dependencies, or runtime behavior.
- Use TDD with a repository-level test that inspects the Makefile and README.
- Do not create a worktree.

---

### Task 1: Add the documented run wrapper

**Files:**
- Create: `Makefile`
- Modify: `README.md: after the installation section`
- Create: `tests/unit/test_makefile.py`

**Interfaces:**
- Consumes: repository root, `.venv/bin/python`, `src` import path, and `tiewtrade.desktop_main` module.
- Produces: `make run`, which expands to the established desktop runtime command.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_makefile.py` with:

```python
from pathlib import Path


def test_makefile_exposes_documented_desktop_run_command() -> None:
    makefile = Path("Makefile").read_text()
    readme = Path("README.md").read_text()

    assert ".PHONY: run" in makefile
    assert "run:" in makefile
    assert "PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main" in makefile
    assert "make run" in readme
```

- [ ] **Step 2: Run the test and verify it fails**

Run `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_makefile.py`.

Expected: FAIL because `Makefile` does not exist.

- [ ] **Step 3: Write the minimal wrapper and documentation**

Create `Makefile`:

```make
.PHONY: run

run:
	PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main
```

Add this section after installation in `README.md`:

```markdown
## Run Desktop App

จาก root ของโครงการ หลังติดตั้งตามขั้นตอนข้างต้นแล้ว ให้รัน:

```bash
make run
```
```

- [ ] **Step 4: Verify wrapper and documentation**

Run `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_makefile.py` and `make -n run`.

Expected: test PASS; `make -n run` prints exactly `PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main`.

- [ ] **Step 5: Commit the implementation**

Run `git add Makefile README.md tests/unit/test_makefile.py && git commit -m "feat: add make run command"`.

### Task 2: Verify repository quality

**Files:**
- Verify: `Makefile`
- Verify: `README.md`
- Verify: `tests/unit/test_makefile.py`

**Interfaces:**
- Consumes: Task 1's run wrapper.
- Produces: verified repository state.

- [ ] **Step 1: Run full verification**

Run `PYTHONPATH=src .venv/bin/python -m pytest -q`, `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src tests`, `.venv/bin/mypy src`, and `git diff --check`.

Expected: all tests and quality gates PASS.

- [ ] **Step 2: Record handoff details**

Report the full test total, `make -n run` result, quality-gate outcomes, implementation commit, and that the branch has not been pushed or merged.
