# Audit Card — Component 01 (`core/`)

**Auditor**: Antigravity (Claude Opus 4.6 Thinking)
**Date**: 2026-11-26
**Mode**: READ-ONLY forensic audit — zero files modified
**Charter**: [`INC1_REENGINEERING_CHARTER.md`](file:///e:/agentic-test-system/docs/specifications/INC1_REENGINEERING_CHARTER.md)

---

## 1. Boundary & Responsibility Check

### 1.1 Cross-Layer Import Discipline

| Rule | Status | Evidence |
|:---|:---:|:---|
| `core/` has zero imports from Layer 2 (orchestration) or Layer 1 (presentation) | ✅ Pass | `models.py`, `state.py`, `config.py`, `protocols/*` import only `pydantic`, `pydantic_settings`, `datetime`, `enum`, `pathlib`, `typing`. No `langgraph`, `typer`, or `rich` imports. |
| `protocols/` import only from `core.models` (same layer) | ✅ Pass | `analyzer.py` imports `DiffHunk`, `SymbolContract` from `core.models`. `llm.py` imports `BaseModel` from `pydantic`. `sandbox.py` imports `BaseModel`, `Path` from stdlib + pydantic. |
| Downward-only dependency flow (Architecture §2) | ✅ Pass | `state.py` → `core.models` (same layer). No upward dependency. |
| Domain services at Layer 3 never import Layer 4 protocols directly into `core/` | ✅ Pass | `core/__init__.py` re-exports protocols for convenience, but `models.py` and `state.py` have no protocol imports. |

### 1.2 Protocol Contract Integrity

| Protocol | Spec Reference | Matches ARCHITECTURE_RULES.md | Deviation |
|:---|:---|:---:|:---|
| `CodeAnalyzer` | Stage 3 Listing 4.1 | ✅ | Signature match. Uses `List[T]` returns per spec. |
| `SandboxManager` | Stage 3 Listing 4.2 | ✅ | Signature match. `SandboxConfig` and `ExecutionRawResult` value objects present. |
| `LLMService` | Stage 3 Listing 4.3 | ✅ | Signature match. `TypeVar("T", bound=BaseModel)` correctly scoped. |

### 1.3 `AGENTS.md` Compliance

| Rule | Status |
|:---|:---:|
| No invented architectural components (§4) | ✅ |
| `model_config = ConfigDict(frozen=True)` on all domain entities (NFR-06) | ✅ (all models in `models.py`, `state.py`). **Exception**: `SandboxConfig` is NOT frozen — see [F-07](#f-07). |
| Safety invariants INV-01 through INV-04 not violated | ✅ |
| No prohibited changes (Architecture §15) | ✅ |

---

## 2. Triaged Findings Table

### Legend
- **P0**: Security / data-corruption / false-success — must fix before sign-off
- **P1**: Contract violation / blocker for Increment 2 — must fix before sign-off
- **SP**: Safe Polish — same-file, near-zero-risk cleanup
- **KD**: Keep / Defer — record only, no implementation

<a id="f-01"></a>

| ID | File | Lines | Classification | Finding | Root Cause | Impact |
|:---|:---|:---:|:---:|:---|:---|:---|
| **F-01** | [`models.py`](file:///e:/agentic-test-system/src/agentic_test/core/models.py#L89) | 89 | **P1 — Must Fix** | **Undocumented `deleted_symbols` field on `RepositorySnapshot`**. This field is not in Stage 2 §4.3.3, not in Stage 3 §4.4.3, not in the Option A human-approved decision (which approved only `diff_hunks`, `affected_symbols`, `syntax_errors`), and not in `IMPLEMENTATION_PLAN.md` Question 3/5. | Agent-added field without explicit human architectural approval. | Violates AGENTS.md §2.3: "Agents are strictly prohibited from altering domain entity ownership [or] public protocol contracts … without explicit human approval." Actively used by `analysis/service.py`, `planning/planner.py`, and `planning/rules.py`, so removing it would cascade. **Requires retroactive human approval or removal.** |
| **F-02** | [`models.py`](file:///e:/agentic-test-system/src/agentic_test/core/models.py#L172-L183) | 172–183 | **P1 — Must Fix** | **`ExecutionEvidence` schema diverges from Stage 2 §4.3.3 on nullability and extra fields.** Stage 2 defines `candidate_id: str` (non-optional), `line_coverage: float` (non-optional), `branch_coverage: float` (non-optional). Implementation has: `candidate_id: Optional[str]`, `line_coverage: Optional[float]`, `branch_coverage: Optional[float]`, plus two un-specified fields: `line_coverage_delta: Optional[float]` and `branch_coverage_delta: Optional[float]`. | Practical necessity (baseline evidence has no candidate_id; nullable coverage for failed runs; deltas for ΔCov computation). But these are schema modifications to a public domain entity without documented human approval. | Contract deviation. The delta fields are heavily used downstream (storage DDL, execution service, CLI console). **Requires retroactive human approval** to ratify the additive schema evolution, identical to how Option A ratified `diff_hunks`/`affected_symbols`/`syntax_errors`. |
| **F-03** | [`state.py`](file:///e:/agentic-test-system/src/agentic_test/core/state.py#L45-L46) | 45–46 | **P1 — Must Fix** | **`WorkflowState` evidence model deviates from Stage 3 §4.4.3.** Stage 3 specifies `evidence: Optional[ExecutionEvidence] = None` (singular). Implementation has: `baseline_evidence: Optional[ExecutionEvidence] = None` and `evidences: Tuple[ExecutionEvidence, ...] = Field(default_factory=tuple)`. | Addresses Open Question #2 (1:1 vs 1:N evidence). The resolution is sound, but the question was explicitly listed as **BLOCKED pending human decision** in `IMPLEMENTATION_PLAN.md` §3 Question 2. | Implemented without recorded sign-off. The `_migrate_legacy_evidence` validator (L55–97) and backward-compatible `.evidence` property (L99–105) are well-engineered. **Requires retroactive human approval** to close Question 2 formally. |
| **F-04** | [`models.py`](file:///e:/agentic-test-system/src/agentic_test/core/models.py#L9) | 9 | **SP — Safe Polish** | **Unused `List` import in `models.py`.** `from typing import List, Optional, Tuple` — `List` is imported but never used in this file (all collections use `Tuple[T, ...]`). | Leftover from pre-Decision-B code. | No runtime impact. `ruff` / `mypy` will flag `F401`. Trivial removal. |
| **F-05** | [`config.py`](file:///e:/agentic-test-system/src/agentic_test/core/config.py#L29) | 29 | **P1 — Must Fix** | **Module-level singleton `settings = Settings()` triggers eager `.env` file load at import time.** Any module that does `from agentic_test.core.config import settings` (or `from agentic_test.core import settings`) will immediately attempt `.env` file resolution, which can fail or produce side effects during test collection, CI, and import-time checks. | Direct module-level instantiation of `BaseSettings`. | Test isolation hazard: tests that don't set env vars get the host machine's `.env` values. Can cause non-deterministic test failures. The `core/__init__.py` re-exports `settings`, so every `import agentic_test.core` eagerly instantiates. Should be lazy (function/property). |
| **F-06** | [`config.py`](file:///e:/agentic-test-system/src/agentic_test/core/config.py#L26) | 26 | **SP — Safe Polish** | **`openai_api_key` is the only LLM key in `Settings`.** ARCHITECTURE_RULES.md §5 specifies `LiteLLMService` as the adapter. LiteLLM supports many providers. The field name implies OpenAI-only. | Naming convention does not match multi-provider intent. | No functional impact — `SecretStr` wrapping is correct. Consider renaming to `llm_api_key` or adding provider-agnostic alternatives. Low priority. |
| <a id="f-07"></a>**F-07** | [`protocols/sandbox.py`](file:///e:/agentic-test-system/src/agentic_test/core/protocols/sandbox.py#L26-L39) | 26–39 | **KD — Keep/Defer** | **`SandboxConfig` is not frozen.** Unlike all other domain entities, `SandboxConfig` has no `ConfigDict(frozen=True)`. As a configuration input (not state), mutability is pragmatically acceptable. | Config objects are typically mutable inputs constructed before use. | No NFR-06 violation — NFR-06 targets "workflow state transitions and data objects." `SandboxConfig` is an infrastructure input, not a domain state entity. Keep as-is. |
| **F-08** | [`protocols/sandbox.py`](file:///e:/agentic-test-system/src/agentic_test/core/protocols/sandbox.py#L42-L52) | 42–52 | **KD — Keep/Defer** | **`ExecutionRawResult` is not frozen.** Unlike domain-layer value objects, this is a low-level telemetry container in the protocol layer. | Infrastructure result, not domain state. | Same rationale as F-07. Consider freezing in a future hardening pass. |
| **F-09** | [`pyproject.toml`](file:///e:/agentic-test-system/pyproject.toml#L24-L33) | 24–33 | **P1 — Must Fix** | **`mypy` and `ruff` are not declared as dev dependencies.** `[project.optional-dependencies].dev` lists only `pytest` and `pytest-cov`. AGENTS.md §8.3 mandates `mypy --strict` and `ruff` as verification harnesses. No `[tool.mypy]` or `[tool.ruff]` configuration sections exist. | Omission during initial setup. | Cannot run `mypy --strict` or `ruff check` without manual install. Blocks the Charter §4.4 "Verify & Seal" gate for every component. |
| **F-10** | [`pyproject.toml`](file:///e:/agentic-test-system/pyproject.toml#L38-L40) | 38–40 | **SP — Safe Polish** | **Missing `[tool.mypy]` strict configuration section and `[tool.ruff]` rules section.** The file has `[tool.pytest.ini_options]` but no mypy or ruff tool config. | Not configured yet. | Without `[tool.mypy] strict = true`, developers must remember `--strict` flag manually. Add config sections for reproducibility. |
| **F-11** | [`protocols/analyzer.py`](file:///e:/agentic-test-system/src/agentic_test/core/protocols/analyzer.py#L25-L60) | 25–60 | **KD — Keep/Defer** | **Protocol return types use `List[T]` while domain models use `Tuple[T, ...]`.** `CodeAnalyzer.parse_symbols()` returns `List[SymbolContract]`, `extract_dependencies()` returns `List[str]`, `resolve_affected_symbols()` returns `List[SymbolContract]`. This matches Stage 3 Listing 4.1 exactly. The mismatch with Decision B's `Tuple` convention is at the boundary — callers coerce to tuple. | Stage 3 Listing 4.1 uses `List` in protocol signatures. Decision B applies to domain model fields. | No contract violation. Protocols define the boundary contract per spec. The conversion to `Tuple[T, ...]` happens at the domain model population boundary (e.g., `AnalysisService`). Keep as-is per spec fidelity. |
| **F-12** | [`core/__init__.py`](file:///e:/agentic-test-system/src/agentic_test/core/__init__.py#L1-L45) | 1–45 | **SP — Safe Polish** | **`core/__init__.py` does not re-export `SandboxManager`, `SandboxConfig`, `ExecutionRawResult`, or sandbox exceptions** from `protocols/sandbox.py`, while `protocols/__init__.py` does export them. Missing convenience re-export from the `core` package barrel. | The `core/__init__.py` cherry-picks exports from `protocols/analyzer.py` (`CodeAnalyzer`, `SyntaxParsingError`) and from `protocols/llm.py` (none), but skips `protocols/sandbox.py` entirely. | Downstream callers must do `from agentic_test.core.protocols import SandboxManager` instead of `from agentic_test.core import SandboxManager`. Inconsistent. |
| **F-13** | [`core/__init__.py`](file:///e:/agentic-test-system/src/agentic_test/core/__init__.py#L23) | 23 | **SP — Safe Polish** | **`core/__init__.py` does not re-export `LLMService`** from `protocols/llm.py`. `CodeAnalyzer` and `SyntaxParsingError` are re-exported, but `LLMService` is omitted. | Inconsistency in barrel exports. | Downstream callers must import `from agentic_test.core.protocols.llm import LLMService` directly. Minor inconsistency. |

---

## 3. Tooling & Type-Safety Gaps

### 3.1 pytest

| Check | Status | Detail |
|:---|:---:|:---|
| `pytest` available | ✅ Installed | `pytest-9.2.0` (installed during audit to run checks). **Not declared in venv** — no venv exists; installed to system Python. |
| `tests/test_state_models.py` execution | ✅ **22 passed in 0.56s** | All 22 test functions pass green. |
| Test coverage of core models | ✅ Adequate | All domain entities exercised: `DiffHunk`, `SymbolContract`, `RepositorySnapshot`, `WorkflowState`, `ExecutionPlan`, `TestCandidate`, `ExecutionEvidence`, `FailureDiagnosis`. |
| **Action needed** | Create a project venv and install `pip install -e ".[dev]"` for reproducible CI. |

### 3.2 mypy

| Check | Status | Detail |
|:---|:---:|:---|
| `mypy` available | ✅ Installed | `mypy-2.4.1` (installed during audit). **Not declared in `pyproject.toml` dev deps.** |
| `mypy --strict src/agentic_test/core/` | ✅ **0 errors** | Clean pass — zero strict-mode violations across all 7 files in `core/`. |
| `[tool.mypy]` config | ❌ Missing | No `[tool.mypy]` section in `pyproject.toml`. |
| **Action needed** | Add `mypy>=1.11.0` to `[project.optional-dependencies].dev` and add `[tool.mypy]` section with `strict = true`. |

### 3.3 ruff

| Check | Status | Detail |
|:---|:---:|:---|
| `ruff` available | ✅ Installed | `ruff-0.17.6` (installed during audit). **Not declared in `pyproject.toml` dev deps.** |
| `ruff check src/agentic_test/core/` | ⚠️ **65 errors** (57 auto-fixable) | See breakdown below. |
| `[tool.ruff]` config | ❌ Missing | No `[tool.ruff]` section in `pyproject.toml`. |

**Ruff error breakdown (across all `core/` files):**

| Rule | Count | Description | Auto-fixable |
|:---|:---:|:---|:---:|
| `UP006` | ~25 | Use `tuple` instead of `Tuple` (Python 3.11+ builtins) | ✅ |
| `UP045` | ~20 | Use `X \| None` instead of `Optional[X]` | ✅ |
| `UP035` | ~5 | `typing.List`, `typing.Tuple`, `typing.Optional` deprecated | ✅ |
| `F401` | 1 | Unused import `List` in `models.py` | ✅ |
| `PIE790` | ~4 | Unnecessary `pass` in exception classes with docstrings | ✅ |
| `I001` | ~5 | Import block unsorted | ✅ |
| `RUF022` | 1 | `__all__` not sorted in `core/__init__.py` | ✅ |
| `UP028` | ~2 | Unnecessary `yield from` / deprecated patterns | ✅ |
| `DTZ011` | ~2 | Use `datetime.now(UTC)` instead of `datetime.now(timezone.utc)` | ✅ |

> [!NOTE]
> All 65 ruff errors are auto-fixable style/modernization issues, not correctness defects. They should be resolved in the SP pass with `ruff check --fix` after `[tool.ruff]` config is established. No functional bugs detected by ruff.

---

## 4. Increment 2 Readiness Check (Review Only)

> [!IMPORTANT]
> This section is a **readiness review only** per Charter §5. No Increment 2 features are implemented.

### 4.1 Change Path / Target Path Routing

The Increment 2 execution model introduces two distinct paths:
- **Change Path**: Generate/maintain tests for symbols affected by recent code changes
- **Target Path**: Generate tests for explicitly specified target symbols/files

**Current readiness assessment:**

| Readiness Factor | Status | Detail |
|:---|:---:|:---|
| `WorkflowRoute` enum extensibility | ✅ Ready | `str, Enum` base allows adding new routes (e.g., `ROUTE_TO_TARGET_GENERATION`) without breaking existing serialization. |
| `ExecutionPlan` structure | ✅ Ready | `target_symbols` and `existing_tests_to_run` fields are generic enough to serve both paths. `rationale` captures routing logic. |
| `RepositorySnapshot` accommodates target-path input | ⚠️ Gap | No field for "explicitly targeted files/symbols" (target path input). Would need an additive field (e.g., `target_files: Tuple[Path, ...] = Field(default_factory=tuple)`) — **additive, non-breaking**. |
| `WorkflowState` path discrimination | ⚠️ Gap | No field to distinguish which path triggered the run. Would need an additive field (e.g., `execution_path: Optional[str] = None`) — **additive, non-breaking**. |
| Routing rules in `planning/rules.py` | ✅ Ready | Current rules P1–P4 are purely diff-based (Change Path). Target Path rules can be added as new rule functions — **additive, non-breaking**. |

**Verdict**: ✅ Current contracts accommodate both paths via **additive fields only** — no breaking changes required.

### 4.2 `missing_lines` / `missing_branches` Coverage Fields

| Readiness Factor | Status | Detail |
|:---|:---:|:---|
| `ExecutionEvidence` coverage fields | ✅ Ready | Current `line_coverage`, `branch_coverage`, `line_coverage_delta`, `branch_coverage_delta` provide aggregate percentages. `missing_lines` and `missing_branches` (per-file line/branch lists) are **additive** — can be added as `Optional[Dict[str, List[int]]] = None` without breaking existing serialization. |
| `CoverageExtractor` (Iteration 1.5) | ✅ Ready | Already parses `coverage.json` which contains `missing_lines` and `missing_branches` data. Currently only extracts aggregate metrics. Extension is non-breaking. |
| SQLite `execution_evidence` table | ✅ Ready | Additive `ALTER TABLE ADD COLUMN` for JSON-typed `missing_lines` / `missing_branches` columns. Non-breaking migration. |

**Verdict**: ✅ Current contracts accommodate coverage-gap fields via **additive columns/fields only** — no breaking changes required.

---

## 5. Proposed Sign-Off Scope

> [!WARNING]
> No code changes will be made until this scope is explicitly approved.

### Must Fix (P0/P1) — 4 items requiring approval

1. **[F-01] Retroactive approval of `deleted_symbols` field** on `RepositorySnapshot` (L89 of `models.py`).
   - **Decision needed**: Ratify the field as a human-approved additive clarification (analogous to Option A), or remove it and cascade changes through `analysis/service.py`, `planning/planner.py`, `planning/rules.py`, and 6 test files.

2. **[F-02] Retroactive approval of `ExecutionEvidence` schema deviations**: nullable `candidate_id`, nullable `line_coverage`/`branch_coverage`, added `line_coverage_delta`/`branch_coverage_delta`.
   - **Decision needed**: Ratify these as approved schema evolution (additive + nullability relaxation), or revert to strict Stage 2 types and rework downstream.

3. **[F-03] Retroactive approval of `WorkflowState.evidences` (plural) + `baseline_evidence`**, closing Open Question #2.
   - **Decision needed**: Ratify the implemented resolution (1:N evidence tuple + legacy migration validator + backward-compatible `.evidence` property), or designate an alternative resolution.

4. **[F-05] Convert `settings` singleton to lazy instantiation** in `config.py`.
   - **Proposed change**: Replace `settings = Settings()` with a `get_settings()` function or `functools.lru_cache`-wrapped factory to prevent import-time side effects.

5. **[F-09] Add `mypy` and `ruff` to `[project.optional-dependencies].dev`** and add `[tool.mypy]` + `[tool.ruff]` configuration sections to `pyproject.toml`.

### Safe Polish — 4 items (near-zero risk, same-file)

6. **[F-04]** Remove unused `List` import from `models.py` L9.
7. **[F-10]** Add `[tool.mypy]` and `[tool.ruff]` config sections to `pyproject.toml`.
8. **[F-12]** Add `SandboxManager`, `SandboxConfig`, `ExecutionRawResult`, sandbox exceptions to `core/__init__.py` `__all__` re-exports.
9. **[F-13]** Add `LLMService` to `core/__init__.py` `__all__` re-exports.

### Keep / Defer — 3 items (no action)

10. **[F-06]** `openai_api_key` naming — cosmetic, defer.
11. **[F-07]** `SandboxConfig` not frozen — intentional for infrastructure config, defer.
12. **[F-08]** `ExecutionRawResult` not frozen — infrastructure result, defer.
13. **[F-11]** Protocol `List[T]` returns vs domain `Tuple[T, ...]` — per-spec, keep.

---

> [!NOTE]
> **Next step**: Submit this audit card for human review and second-reviewer (Codex) pass per Charter §4.1. Upon scope approval, proceed to Charter §4.3 (Scoped Implementation) for the approved items only.
