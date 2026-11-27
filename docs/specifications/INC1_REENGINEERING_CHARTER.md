# Increment 1 Micro-Charter

## 1. Goal and Scope

Re-engineer and harden **Increment 1** component by component, using a **bottom-up** approach, to ensure that contracts are stable before building Increment 2 and Increment 3.

Do not add any new architectural component unless it is explicitly approved in `AGENTS.md` and `ARCHITECTURE_RULES.md`.

## 2. Approved Component Order

The approved sequence consists of **10 components plus a final Exit Gate**.

The `storage/` component is fixed at position 08, after `execution/` and `diagnosis/`. This prevents SQLite tables and migrations from being modified twice after the `ExecutionEvidence` and coverage fields have been finalized.

| Order | Component | Included files |
|---:|---|---|
| 01 | `core/` — Core and contracts | `models.py`, `state.py`, `config.py`, `protocols/*`, `pyproject.toml` |
| 02 | `analysis/` — Static analysis | `git_service.py`, `ast_analyzer.py`, `discovery.py`, `service.py` |
| 03 | `planning/` — Deterministic planning | `rules.py`, `planner.py` |
| 04 | `generation/` — Test generation | `context.py`, `schemas.py`, `exceptions.py`, `litellm_service.py`, `mock_llm.py`, `service.py` |
| 05 | `validation/` — Validation gates | `base.py`, `models.py`, `syntax.py`, `security.py`, `collection.py`, `pipeline.py` |
| 06 | `execution/` + `docker/` — Execution and coverage | `staging.py`, `runner.py`, `coverage.py`, `docker_sandbox.py`, `mock_sandbox.py`, `service.py`, `Dockerfile.runner` |
| 07 | `diagnosis/` — Failure diagnosis | `rules.py`, `llm_classifier.py`, `service.py` |
| 08 | `storage/` — Relational storage | `database.py`, `events.py`, `repository.py` |
| 09 | `workflow/` — Orchestration and recovery | `edges.py`, `nodes.py`, `engine.py`, `recovery.py` |
| 10 | `cli/` — CLI and service wiring | `console.py`, `service_factory.py`, `app.py` |
| 11 | **Exit Gate** — Final closure gate | Architecture-boundary tests; live-scenario execution with Docker; synchronization of `ARCHITECTURE_RULES.md` and `IMPLEMENTATION_PLAN.md`; and creation of the `v1.0-inc1-stable` reference tag. |

## 3. Three-Way Classification Rule

Before changing any line within a component, classify every finding into one of the following categories:

### A. Must Fix — P0/P1

Fix immediately if the issue involves any of the following:

- A security vulnerability.
- Data corruption.
- A false success report.
- A lifecycle defect.
- A contract violation.
- A genuine blocker to Increment 2.

### B. Safe Polish

A narrowly scoped cleanup or improvement confined to the same file, with near-zero cost and risk.

### C. Keep / Defer

Record but do not implement:

- Architectural improvements that do not affect correctness or safety.
- Any feature belonging to Increment 2 or Increment 3.

## 4. Workflow and Definition of Stable

Each component must pass through the following workflow before moving to the next component:

1. **Read-Only Forensic Audit (Audit Card)**
   - Read the component's code and its tests in full.
   - Record known defects and newly discovered issues.
   - Do not modify any file during this stage.
   - Pass the audit card to Codex for review as a second reviewer.

2. **Scope Approval (Sign-Off)**
   - The team must approve the list of mandatory fixes and deferred items before any code is written.

3. **Scoped Implementation (Fix & Test)**
   - Implement a coherent set of fixes limited to the approved component scope.
   - Add or update characterization and acceptance tests to prove every fix and relevant edge case.

4. **Verification and Closure (Verify & Seal)**
   - Run and pass the complete `pytest`, `mypy`, and `ruff` checks.
   - Verify integration with the preceding and following components.
   - Review `git diff` before creating a clean commit.
   - Do not proceed to the next component while any P0/P1 defect or unresolved contract remains.

## 5. Future-Readiness Constraint (Increment 2 Readiness)

When reviewing the contracts in `core/`, `analysis/`, and `execution/`, check that they can later accommodate both execution paths:

- `Change Path`
- `Target Path`

Also check that the coverage-gap fields can be accommodated:

- `missing_lines`
- `missing_branches`

This is a **readiness review only**. Do not implement Increment 2 features during Increment 1.

This charter is intended to serve as the shared reference for every work session with Antigravity and Codex.
