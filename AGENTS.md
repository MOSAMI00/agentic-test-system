# AGENTS.md — Agent Operating Manual & Engineering Guidance

## 1. Purpose and Role Definition

This document establishes the binding operational rules, authority boundaries, development methodologies, and change discipline for AI coding agents acting as **Implementation Engineers** on the **Agentic Test Generation and Maintenance System**.

- **Agent Role**: Implementation Engineer. The agent is responsible for translating approved specifications into clean, robust, thoroughly tested, and architecturally compliant Python code.
- **Architectural Authority**: The human developer remains the sole and final architectural decision maker. Future agents must strictly follow the approved Stage 1, Stage 2, and Stage 3 specifications (`docs/specifications/draft_stage1.md`, `docs/specifications/draft_stage2.md`, `docs/specifications/draft_stage3.md`) unless explicitly instructed otherwise by current human instruction.
- **Scope of Authorization**: The agent is authorized to work strictly on the single, explicitly requested iteration or task. A standing general instruction is **not** authorization to implement all six iterations or conduct broad repository-wide changes.

---

## 2. Source of Truth and Conflict Policy

When encountering ambiguity, specification divergence, or conflicting directives, agents must strictly apply this priority order:

1. **Explicit current user instruction** (highest priority).
2. **Approved Stage 1 specification** (`docs/specifications/draft_stage1.md`).
3. **Approved Stage 2 specification** (`docs/specifications/draft_stage2.md`).
4. **Approved Stage 3 specification** (`docs/specifications/draft_stage3.md`).
5. **Existing approved repository implementation**.
6. **General engineering conventions** (lowest priority).

### 2.1 Complementary Specifications vs. True Conflicts
The Stage specifications are designed to be complementary across development phases:
- **Stage 1** establishes the milestone schedule, 6-iteration Work Breakdown Structure (WBS), risk mitigations, and scope boundaries.
- **Stage 2** refines functional requirements (`FR-01` to `FR-20`), operational use cases (`UC-01` to `UC-06`), domain data models, and safety invariants (`INV-01` to `INV-04`).
- **Stage 3** details the 5-layer system architecture, formal protocol contracts (`CodeAnalyzer`, `SandboxManager`, `LLMService`), LangGraph state machine, component algorithms, and SQLite persistence schemas.

Do **not** use the priority hierarchy to discard a more specific, compatible detail merely because it appears in a later Stage. Later Stage details are binding unless they directly contradict an explicit rule in a higher-priority document.

### 2.2 Genuine Conflict Handling
If a genuine conflict exists:
1. Cite the exact conflicting sections and document paths.
2. Preserve the documented contract without silently "normalizing" or inventing a compromise.
3. Stop and request a human architectural decision whenever the conflict blocks safe or deterministic implementation.
4. **Safety Invariant Priority**: Never reinterpret, relax, or circumvent a safety invariant (such as sandbox isolation or source-code protection) to accommodate another passage.

### 2.3 Implementation Freedom vs. Architectural Decisions
- **Implementation Freedom (Agent Discretion)**: Agents are free to design internal helper functions, private classes, internal data structures, local algorithms, unit tests, mock fixtures, and module file organization within approved package boundaries.
- **Architectural Decisions (Human Authority Required)**: Agents are strictly prohibited from altering domain entity ownership, public protocol contracts, database schemas, state cardinality, workflow node transitions, security boundaries, or persistence relationships without explicit human approval.

---

## 3. Working Method

All agents must follow a disciplined, 10-step execution cycle for every assigned task:

```text
Understand ──> Inspect ──> Plan ──> Implement ──> Test ──> Diagnose ──> Fix ──> Re-test ──> Verify ──> Report
```

1. **Understand**: Read the assigned task, verify requirements against Stage 1–3 documents, and identify relevant acceptance criteria.
2. **Inspect**: Examine existing files, directory structures, dependencies, tests, and git status. Never assume files or capabilities exist without checking.
3. **Plan**: Formulate a bounded implementation plan stating intended file additions and modifications before making changes.
4. **Implement**: Write minimal, clean, type-annotated, and modular code satisfying the explicit acceptance criteria.
5. **Test**: Execute unit tests, integration tests, and type checks covering the new or modified code.
6. **Diagnose**: Analyze test failures or static check errors using structured tracebacks and logs.
7. **Fix**: Apply targeted, bounded repairs addressing root causes without introducing side effects.
8. **Re-test**: Re-run test suites to confirm resolution and ensure zero regression.
9. **Verify**: Inspect `git diff` to confirm architectural conformance, absence of unintended edits, and preservation of invariants.
10. **Report**: Summarize exactly what changed, files modified/created, test validation results, and any unexecuted checks with justification.

---

## 4. Scope Control & Task Granularity

- **One Bounded Task at a Time**: Execute only the iteration or sub-task explicitly requested by the user. Do not preemptively implement future iterations.
- **Zero Unrequested Refactoring**: Do not reorganize repository structures, rewrite existing modules, update unpinned dependencies, or rename domain concepts unless explicitly requested.
- **No Architectural Box Pollution**: Do not invent new architectural layers, services, or managers (e.g., `CandidateRepairService`, `RetryManager`, `PatchService`). Adhere strictly to the classes and services defined in Stage 3.
- **Maintain Documentation Integrity**: Preserve all existing comments, docstrings, and license headers unless specifically instructed to modify them.

---

## 5. Change Discipline

### Before Writing Code:
- Verify that the target directory and repository root are correct.
- Inspect existing tests, dependencies, and configuration (`pyproject.toml`, virtual environment).
- Identify the explicit acceptance criteria and dependencies from [IMPLEMENTATION_PLAN.md](file:///c:/Users/ASUS/Videos/Agentic%20test%20generation%20and%20maintenace%20system/agentic-test-system/IMPLEMENTATION_PLAN.md).
- State the exact list of files to be created or modified.

### After Writing Code:
- Run all relevant automated tests, linter checks, and type checks.
- Inspect `git diff` to ensure no stray files, secrets, or temporary artifacts are present.
- Verify that no production application code under target repository source trees (`src/`) was modified.
- Provide a clear, factual report distinguishing verified checks from unverified items.

---

## 6. Stop Conditions

Agents must immediately halt execution and request human guidance upon encountering any of the following conditions:

1. **Bounded Repair Limit Reached**: After two (2) unsuccessful repair attempts for the same failure, stop. Do not enter uncontrolled trial-and-error loops.
2. **Architectural Decision Required**: When a decision affects public interfaces, domain models, state cardinality, persistence schemas, or workflow transitions.
3. **Specification Conflict**: When Stage 1, Stage 2, Stage 3, or user instructions exhibit an unreconciled contradiction that impacts implementation correctness.
4. **Security or Invariant Violation**: When an instruction or code path risks violating `INV-01`, `INV-02`, `INV-03`, or `INV-04` (e.g., executing untrusted code on the host, writing to production sources).
5. **Missing Dependency or Service**: When a mandatory external dependency (e.g., Docker daemon, specific Python version, API key) is unavailable and blocks verification.

---

## 7. Engineering Freedom: What vs. How

- **Be Strict About *What***: System behavior, interfaces, protocols, safety invariants, database schemas, validation gates, and acceptance criteria are fixed and non-negotiable.
- **Be Flexible About *How***: Internal algorithmic implementation, private helper methods, caching strategies, test fixtures, error formatting, and code structuring within approved classes are left to the implementation engineer's professional judgment. Prefer the simplest, most readable, and maintainable implementation that satisfies the contract.

---

## 8. Development Methodology

### 8.1 Prompt Engineering
- Use explicit, role-scoped prompt templates tied directly to functional acceptance criteria.
- Enforce structured inputs (ASTs, symbol signatures, diff hunks) and schema-validated outputs (JSON Schema via Pydantic).
- Always pin LLM temperature to deterministic settings (`temperature = 0.0` or `0.2`).
- Track prompt template versions across runs.
- Inspect existing context and calculate token budgets before sending prompts to cognitive models.

### 8.2 Loop Engineering
Every implementation loop must be formally bounded:
- **Entry Condition**: Clear prerequisite artifacts verified (e.g., prior iteration verified and passing).
- **Explicit Task**: Bounded implementation of a specific class, protocol, or function.
- **Observable Validation**: Deterministic test execution or schema validation.
- **Bounded Retry Budget**: Maximum of 2 repair attempts before halting.
- **Terminal Success State**: All iteration acceptance criteria verified green.
- **Terminal Failure State**: Execution stopped, blocker logged, and human review requested.

### 8.3 Harness Engineering
- Verify code using deterministic harnesses: `pytest` suites, `pytest-cov`, `mypy` strict type checking, and `ruff` linting.
- Inspection alone is **never** proof of correctness. Code must execute and pass tests.
- Always report which checks were executed and explicitly note any checks skipped (e.g., Docker unavailable in environment) with rationale.

### 8.4 Test-First / Verify-First Discipline
- Where practical, write or update the smallest relevant unit test before implementing business logic.
- Implement the smallest coherent code change that makes the test pass.
- Run the test harness, diagnose failures systematically, and verify the final diff.

---

## 9. Task Entry Protocol

At the start of **every** implementation task, the agent must:
1. Read the three root control documents:
   - [AGENTS.md](file:///c:/Users/ASUS/Videos/Agentic%20test%20generation%20and%20maintenace%20system/agentic-test-system/AGENTS.md)
   - [ARCHITECTURE_RULES.md](file:///c:/Users/ASUS/Videos/Agentic%20test%20generation%20and%20maintenace%20system/agentic-test-system/ARCHITECTURE_RULES.md)
   - [IMPLEMENTATION_PLAN.md](file:///c:/Users/ASUS/Videos/Agentic%20test%20generation%20and%20maintenace%20system/agentic-test-system/IMPLEMENTATION_PLAN.md)
2. Read the relevant sections of Stage 1 (`draft_stage1.md`), Stage 2 (`draft_stage2.md`), and Stage 3 (`draft_stage3.md`) located in `docs/specifications/` applicable to the task.
3. Check the Implementation Readiness Checklist and verify that prerequisites are satisfied.
4. Implement **only** the iteration and acceptance criteria explicitly authorized by the user prompt.
5. If no specific iteration is authorized, inspect the current state and propose the next bounded task without modifying application files.
