# IMPLEMENTATION_PLAN.md — Increment 1 Implementation Plan

## 1. Overview and Execution Strategy

This implementation plan provides the definitive, bottom-up engineering roadmap for **Increment 1: Automated Unit Test Generation & Sandboxed Verification Baseline** of the Agentic Test Generation and Maintenance System.

### 1.1 Strict Sequential Progression
Implementation proceeds strictly through six sequential iterations:
```text
Iteration 1.1 ──> verify ──> Iteration 1.2 ──> verify ──> Iteration 1.3 ──> verify ──>
Iteration 1.4 ──> verify ──> Iteration 1.5 ──> verify ──> Iteration 1.6 ──> verify
```
- **Rule**: An iteration must not be marked complete and work must not begin on subsequent iterations until all acceptance criteria of the current iteration are implemented and verified.
- **Exceptions**: Any deviation or exception requires explicit written authorization from the human architectural decision maker. A blocked security-sensitive criterion is never considered a pass.

### 1.2 Current Repository Baseline Preflight Findings
- **Repository Root**: `c:\Users\ASUS\Videos\Agentic test generation and maintenace system\agentic-test-system`
- **Git Repository Status**: Initialized (`.git/` directory initialized; clean repository, no commits yet).
- **Existing Source Assets**: Clean implementation repository layout. Authoritative specification copies located under `docs/specifications/`.
- **Specification Source Paths**:
  - Stage 1: `docs/specifications/draft_stage1.md`
  - Stage 2: `docs/specifications/draft_stage2.md`
  - Stage 3: `docs/specifications/draft_stage3.md`

### 1.3 Implementation Readiness Checklist

| Iteration / Workstream | Implementation Status | Readiness & Blocker Details |
| :--- | :---: | :--- |
| **Iteration 1.1: Core Infrastructure & Ingestion** | **COMPLETED & VERIFIED — pending no further 1.1 work; Gate 3, test-to-symbol mapping, and multi-candidate evidence remain open for later iterations.** | Implemented in dedicated repo `agentic-test-system` (39 tests passing, 95% coverage, strict mypy 0 issues). Decision A (remote origin via `GitService.get_remote_origin()`, unpersisted in `RepositorySnapshot`), Decision B (deep collection immutability via `Tuple[T, ...]` replacing `List[T]` for NFR-06), FR-02 binary exclusion, and human-approved Option 1 clarification for FR-01 configuration clause (standard Git layout validation) fully verified. |
| **Iteration 1.2: AST Parsing & Symbol Mapping** | **BLOCKED (Design Heuristic Required)** | Blocked on Open Question #4 (test-to-symbol mapping heuristic) and dependency on Iteration 1.1 models. *Note: `affected_symbols` is populated during this iteration.* |
| **Iteration 1.3: Deterministic Planning Gate** | **READY (Specification-Aligned)** | Pure Python routing rules P1–P4 and route precedence logic fully defined; blocked only by dependency on Iterations 1.1/1.2. |
| **Iteration 1.4: Generation & Multi-Gate Validation** | **BLOCKED (Safety Invariant Conflict)** | Gate 3 host-side `pytest --collect-only` conflicts with host execution prohibition (`INV-01`, `RSK-02`). Kept strictly blocked pending human security decision. |
| **Iteration 1.5: Docker Sandbox & Coverage** | **BLOCKED (State Modeling Decision Required)** | Blocked on Open Question #2 (`WorkflowState.evidence` 1:1 vs. 1:N multi-candidate evidence association). |
| **Iteration 1.6: Dual-Engine Triage & CLI** | **READY (Specification-Aligned)** | 7-category taxonomy, SQLite schema, LangGraph state machine, and Typer CLI fully specified; dependent on upstream iterations. |

---

## 2. Iteration Breakdown

---

### Iteration 1.1 — Core Infrastructure, State Contracts & Repository Ingestion

- **Objective**: Establish the Python package structure, foundational configuration, immutable Pydantic domain models, workflow state contracts, and deterministic Git repository ingestion.
- **Scope**:
  - Python project layout setup (`agentic_test` package).
  - Pydantic v2 domain models and immutable `WorkflowState` definitions.
  - `GitService` implementation for repository validation, commit extraction, and unified diff computation.
  - Exclusion filtering for virtual environments, caches, and `.git` internal metadata.
- **Required Modules / Files**:
  - `pyproject.toml` (Project configuration, dependencies, test runner settings)
  - `src/agentic_test/__init__.py`
  - `src/agentic_test/core/__init__.py`
  - `src/agentic_test/core/models.py` (Domain entities: `RepositorySnapshot`, `DiffHunk`, `ChangeType`)
  - `src/agentic_test/core/state.py` (Immutable `WorkflowState` model)
  - `src/agentic_test/core/config.py` (Settings via `pydantic-settings`)
  - `src/agentic_test/analysis/__init__.py` (or `src/agentic_test/infrastructure/git.py`)
  - `src/agentic_test/analysis/git_service.py` (`GitService` adapter)
  - `tests/__init__.py`
  - `tests/test_git_service.py`
  - `tests/test_state_models.py`
- **Dependencies on Previous Iterations**: None (Entry baseline).
- **Required Classes / Interfaces**:
  - `RepositorySnapshot` (Pydantic model; Stage 2 Section 4.3.3 clarified via Option A and Decision B: preserves the 9 baseline fields, and adds `diff_hunks: Tuple[DiffHunk, ...] = Field(default_factory=tuple)`, `affected_symbols: Tuple[SymbolContract, ...] = Field(default_factory=tuple)`, and `syntax_errors: Tuple[str, ...] = Field(default_factory=tuple)` as immutable, typed fields with empty-tuple defaults). Collection fields use `Tuple[T, ...]` for deep immutability under `NFR-06`.
  - `DiffHunk` (Pydantic value object; Stage 2 Section 4.3.3, frozen=True)
  - `ChangeType` (Enum: `ADDED`, `MODIFIED`, `DELETED`)
  - `WorkflowState` (Pydantic model; Stage 3 Section 4.4.3 clarified via Decision B: concrete typing for `plan: Optional[ExecutionPlan]`, `candidates: Tuple[TestCandidate, ...]`, `evidence: Optional[ExecutionEvidence]`, `diagnoses: Tuple[FailureDiagnosis, ...]`).
  - `GitService` (Class with methods: `validate_repository(path: Path) -> bool`, `get_head_commit() -> str`, `get_remote_origin() -> Optional[str]`, `compute_diff(base_commit: str) -> Tuple[DiffHunk, ...]`, `create_snapshot(...) -> RepositorySnapshot`)
  - `RepositoryValidationError` (Custom exception)
- **Required Tests**:
  - `tests/test_git_service.py`:
    - Valid local git repository correctly identified; non-git directories rejected with `RepositoryValidationError`. Tests create isolated transient Git repositories using pytest `tmp_path`.
    - Correct extraction of HEAD commit SHA, branch name, and base commit.
    - Unified diff parser parses `git diff` hunks with line spans (`old_start`, `old_lines`, `new_start`, `new_lines`) across unstaged, staged, and committed changes.
    - Remote origin extraction (`get_remote_origin()`): returns origin URL when present, `None` when absent or upstream-only, raises `RepositoryValidationError` on config/read error (FR-01, Decision A).
    - Exclusions properly ignore `.git`, `.venv`, `venv`, `__pycache__`, non-code files, and binary files (tested across staged, committed, and untracked binary files with null-byte detection).
    - Error boundaries: verifies GitCommandError during `list_tracked_files` and `compute_diff` raises `RepositoryValidationError` rather than silently swallowing errors.
  - `tests/test_state_models.py`:
    - `RepositorySnapshot` and `WorkflowState` enforce immutable instantiation, serialization, and validation.
    - Verifies that `diff_hunks`, `affected_symbols`, and `syntax_errors` default to independent empty tuples (Option A).
    - Verifies deep collection immutability (`NFR-06` / Decision B): in-place mutation attempts (`.append()`, `.extend()`, `.pop()`, `[0] = ...`) on non-empty collections raise `AttributeError` or `TypeError`.
    - Verifies independence from caller mutable input lists (Pydantic converts input lists to independent tuples).
    - Verifies nested entity mutation prevention (`frozen=True` on nested elements).
    - Verifies standard JSON array serialization and round-trip fidelity (`model_dump_json()` and `model_validate_json()`).
    - Verifies `model_copy(update={...})` behavior with immutable collections.
    - Verifies validated state reconstruction rejecting invalid replacement types and coercing valid replacements to tuples.
- **Acceptance Criteria**:
  - Satisfies `FR-01`: Directory validated, HEAD SHA (40-hex chars) extracted, remote origin parsed via `GitService.get_remote_origin()` (not persisted in `RepositorySnapshot` per Decision A), `snapshot.is_valid = True`. *Note: The clause "verify that required configuration files exist" is resolved for Increment 1 under human-approved Option 1: standard Git repository configuration and structural references (`.git` directory, metadata, and `HEAD` reference) satisfy repository validation without imposing project-level Python configuration files (see Question #6).*
  - Satisfies `FR-02`: Unified diff hunks parsed without executing target code; `.git`, virtualenvs, and real binary files ignored.
  - Satisfies `NFR-01` (Static Analysis Safety) and `NFR-06` (Deep State Immutability via standard immutable `Tuple[T, ...]` containers per Decision B).
- **Definition of Done**:
  - All classes and tests implemented.
  - `pytest tests/test_git_service.py tests/test_state_models.py` passes 100% green.
  - Strict type checking (`mypy --strict`) passes with zero errors on all Iteration 1.1 modules.
- **What Must NOT Be Implemented Yet**:
  - AST parsing and symbol extraction (deferred to Iteration 1.2).
  - Decision planning logic (deferred to Iteration 1.3).
  - LLM service integration or prompt templates (deferred to Iteration 1.4).
  - Docker sandbox management (deferred to Iteration 1.5).
  - SQLite persistence, CLI, and LangGraph StateGraph (deferred to Iteration 1.6).

---

### Iteration 1.2 — AST Parsing, Symbol Extraction & Static Dependency Analysis

- **Objective**: Implement language-agnostic static analysis protocol, Python AST traversal, symbol interface extraction, existing test discovery, and diff-to-symbol intersection.
- **Scope**:
  - Formal `CodeAnalyzer` protocol definition.
  - `PythonASTAnalyzer` implementation using standard library `ast`.
  - Symbol extraction (functions, methods, classes, type hints, signatures, line ranges, docstrings).
  - Module dependency extraction.
  - `TestDiscovery` service to locate existing `pytest` test files and map tested symbols.
  - Intersection of `DiffHunk` line spans with symbol boundaries to populate `affected_symbols`.
- **Required Modules / Files**:
  *(Note: To be created upon explicit human authorization)*
  - `src/agentic_test/core/protocols/__init__.py`
  - `src/agentic_test/core/protocols/analyzer.py` (`CodeAnalyzer` protocol; Stage 3 Listing 4.1)
  - `src/agentic_test/analysis/ast_analyzer.py` (`PythonASTAnalyzer` adapter)
  - `src/agentic_test/analysis/discovery.py` (`TestDiscovery` service)
  - `src/agentic_test/analysis/service.py` (`AnalysisService` facade)
  - `tests/test_ast_analyzer.py`
  - `tests/test_discovery.py`
- **Dependencies on Previous Iterations**: Iteration 1.1 (`RepositorySnapshot`, `DiffHunk`, `GitService`).
- **Required Classes / Interfaces**:
  - `CodeAnalyzer` (Formal `typing.Protocol` with `@runtime_checkable`; Stage 3 Listing 4.1)
  - `SymbolContract` (Entity; Stage 2 Section 4.3.3)
  - `SymbolType` (Enum: `FUNCTION`, `METHOD`, `CLASS`, `MODULE`)
  - `PythonASTAnalyzer` (Concrete realization of `CodeAnalyzer`)
  - `TestDiscovery` (Class with methods: `discover_test_suites(repo_path: Path) -> List[Path]`, `extract_tested_symbols(test_file: Path) -> List[str]`)
  - `AnalysisService` (High-level facade coordinating git, ast, and discovery services)
- **Required Tests**:
  - `tests/test_ast_analyzer.py`:
    - AST traversal on Python 3.11+ source files extracts functions, async functions, classes, and methods.
    - Accurate extraction of parameters, type annotations, return types, line spans $[lineno, end\_lineno]$, and docstrings.
    - Malformed Python files log syntax errors in `snapshot.syntax_errors` without halting parsing of valid files.
    - Diff intersection correctly tags symbols as `is_affected = True` when diff line spans overlap $[start\_line, end\_line]$.
    - Zero execution of target code (mock import verify).
  - `tests/test_discovery.py`:
    - Discovers `test_*.py` and `*_test.py` files in test suites.
- **Acceptance Criteria**:
  - Satisfies `FR-03`: Pure static parsing of source files, extracting symbols and line ranges with 100% precision.
  - Satisfies `FR-04`: Cross-references diff hunks with symbol spans to determine and populate `affected_symbols`.
  - Satisfies `NFR-11` (Modular Language Boundaries) and `INV-02` (Source read-only protection).
- **Definition of Done**:
  - `PythonASTAnalyzer` fully implements `CodeAnalyzer`.
  - All unit tests pass green.
  - AST analysis verified to execute zero target application code.
- **What Must NOT Be Implemented Yet**:
  - Execution planning and routing rules (deferred to Iteration 1.3).
  - Cognitive generation or prompts (deferred to Iteration 1.4).
  - Docker sandbox (deferred to Iteration 1.5).
  - Persistence and CLI (deferred to Iteration 1.6).

---

### Iteration 1.3 — Deterministic Planning & Decision Routing Gate

- **Objective**: Implement pure Python deterministic routing rules that evaluate diffs and symbol coverage to choose execution paths without cognitive LLM calls.
- **Scope**:
  - Pure Python precedence rules (Rules P1 through P4).
  - Deterministic route selection: `ROUTE_NO_OP`, `ROUTE_TO_DOCKER_EXECUTION`, `ROUTE_TO_TEST_GENERATION`.
  - Structured `ExecutionPlan` construction with cryptographic decision hash.
  - Topological sorting of target symbols for test generation ordering.
- **Required Modules / Files**:
  *(Note: To be created upon explicit human authorization)*
  - `src/agentic_test/planning/__init__.py`
  - `src/agentic_test/planning/planner.py` (`ExecutionPlanner`)
  - `src/agentic_test/planning/rules.py` (Precedence rules P1–P4)
  - `tests/test_execution_planner.py`
- **Dependencies on Previous Iterations**: Iteration 1.1 (`RepositorySnapshot`), Iteration 1.2 (`SymbolContract`, `affected_symbols`).
- **Required Classes / Interfaces**:
  - `WorkflowRoute` (Enum: `ROUTE_NO_OP`, `ROUTE_TO_DOCKER_EXECUTION`, `ROUTE_TO_TEST_GENERATION`; Stage 2 Section 4.3.3)
  - `ExecutionPlan` (Entity; Stage 2 Section 4.3.3)
  - `ExecutionPlanner` (Class with method: `plan(snapshot: RepositorySnapshot) -> ExecutionPlan`)
- **Required Tests**:
  - `tests/test_execution_planner.py`:
    - Clean tree (zero diff hunks) $\rightarrow$ `ROUTE_NO_OP` (Rule P1).
    - Non-code modifications only (`*.md`, `*.yaml`) $\rightarrow$ `ROUTE_NO_OP` (Rule P2).
    - Affected symbols covered by existing tests $\rightarrow$ `ROUTE_TO_DOCKER_EXECUTION` (Rule P3).
    - Uncovered affected symbols detected $\rightarrow$ `ROUTE_TO_TEST_GENERATION` (Rule P4).
    - Verification that zero LLM or network calls occur during planning (`INV-03`).
    - Cryptographic decision hash verification.
- **Acceptance Criteria**:
  - Satisfies `FR-05`: Deterministic route selection across all boundary conditions.
  - Satisfies `FR-06`: Structured `ExecutionPlan` with unique ID, route, and target symbols.
  - Satisfies `NFR-02` (Deterministic Route Precedence) and `INV-03` (Zero-LLM Gate).
- **Definition of Done**:
  - `ExecutionPlanner` unit tests pass with 100% branch coverage.
  - Mathematical verification that cognitive model is never invoked when existing tests suffice.
- **What Must NOT Be Implemented Yet**:
  - LLM test synthesis (deferred to Iteration 1.4).
  - Validation pipeline (deferred to Iteration 1.4).
  - Docker container execution (deferred to Iteration 1.5).
  - CLI and database storage (deferred to Iteration 1.6).

---

### Iteration 1.4 — Cognitive Test Generation & Multi-Gate Validation Pipeline

- **Objective**: Implement the `LLMService` protocol, `LiteLLMService` gateway, symbol-scoped context assembly, structured `pytest` test generation with internal bounded repair, and the non-regenerative multi-gate validation pipeline.
- **Scope**:
  - Formal `LLMService` protocol definition.
  - `LiteLLMService` adapter with exponential backoff and structured output schema parsing.
  - `ContextAssembler` with token budget enforcement ($\le 4,000$ tokens).
  - `GenerationService` with internal candidate repair ($R_{max}=2$ retries) for repairable defects.
  - `ValidationPipeline` with Gate 1 (`SyntaxValidator` via `ast.parse`) and Gate 2 (`SecurityValidator` via Bandit AST visitor).
  - Quarantine mechanism for rejected candidates.
  - **Notice**: Gate 3 (`CollectionValidator`) is blocked pending resolution of Open Architectural Question #1.
- **Required Modules / Files**:
  *(Note: To be created upon explicit human authorization)*
  - `src/agentic_test/core/protocols/llm.py` (`LLMService` protocol; Stage 3 Listing 4.3)
  - `src/agentic_test/generation/__init__.py`
  - `src/agentic_test/generation/context.py` (`ContextAssembler`)
  - `src/agentic_test/generation/generator.py` (`GenerationService`)
  - `src/agentic_test/infrastructure/litellm_service.py` (`LiteLLMService` adapter)
  - `src/agentic_test/validation/__init__.py`
  - `src/agentic_test/validation/base.py` (`BaseValidator`, `ValidationResult`)
  - `src/agentic_test/validation/gates.py` (`SyntaxValidator`, `SecurityValidator`, `CollectionValidator`)
  - `src/agentic_test/validation/pipeline.py` (`ValidationPipeline`)
  - `tests/test_context_assembler.py`
  - `tests/test_generation_service.py`
  - `tests/test_validation_pipeline.py`
- **Dependencies on Previous Iterations**: Iteration 1.1 (models), Iteration 1.2 (`SymbolContract`), Iteration 1.3 (`ExecutionPlan`).
- **Required Classes / Interfaces**:
  - `LLMService` (Protocol; Stage 3 Listing 4.3)
  - `GenerationContext` (Value object; Stage 2 Section 4.3.3)
  - `TestCandidate` (Entity; Stage 2 Section 4.3.3)
  - `ValidationStatus` (Enum: `PENDING`, `PASSED`, `REJECTED_SYNTAX`, `REJECTED_SECURITY`, `REJECTED_COLLECTION`, `QUARANTINED`)
  - `ContextAssembler` (`assemble_prompt`)
  - `GenerationService` (`generate`, `regenerate_with_feedback`)
  - `BaseValidator` (Abstract base class)
  - `SyntaxValidator` (Gate 1), `SecurityValidator` (Gate 2), `CollectionValidator` (Gate 3)
  - `ValidationPipeline` (`validate`)
- **Required Tests**:
  - `tests/test_context_assembler.py`:
    - Context promptly formatted with symbol AST, docstrings, and diff hunks.
    - Context truncated cleanly when exceeding 4,000 tokens.
  - `tests/test_generation_service.py`:
    - Mocked LLM structured response parsed into `TestCandidate`.
    - Internal repair triggers up to 2 retries on malformed syntax, producing repaired candidate.
    - Unrecoverable syntax errors after 2 retries marked `MALFORMED_OUTPUT` and quarantined.
  - `tests/test_validation_pipeline.py`:
    - Gate 1 rejects non-syntactic candidates (`REJECTED_SYNTAX`) directly to quarantine.
    - Gate 2 AST visitor blocks dangerous calls (`os.system`, `subprocess`, `eval`, `exec`, socket connections) with `REJECTED_SECURITY`.
    - Non-regenerative invariant: Validation pipeline triggers zero calls to `GenerationService`.
- **Acceptance Criteria**:
  - Satisfies `FR-07`: Bounded context prompt assembled within token ceiling.
  - Satisfies `FR-08`: Structured unit test candidate generated with bounded internal repair ($R_{max}=2$).
  - Satisfies `FR-09` & `FR-10`: Multi-gate pre-execution screening rejects and quarantines invalid candidates with zero regeneration loops.
  - Satisfies `NFR-04` (Context Window Bounding), `NFR-13` (LLM Independence), `RSK-01`, and `RSK-02`.
- **Definition of Done**:
  - Unit tests pass green using mock LLM adapters.
  - Security AST scanner successfully detects 100% of synthetic unsafe patterns.
  - Validation pipeline proven non-regenerative.
- **What Must NOT Be Implemented Yet**:
  - Gate 3 host execution (BLOCKED pending human decision).
  - Docker sandbox management (deferred to Iteration 1.5).
  - Dual-engine failure diagnosis, CLI, and LangGraph (deferred to Iteration 1.6).

---

### Iteration 1.5 — Isolated Docker Sandbox Execution & Coverage Measurement

- **Objective**: Implement the `SandboxManager` protocol, `DockerSandboxManager` adapter, hardened runner container configuration, test runner execution, and coverage extraction.
- **Scope**:
  - Formal `SandboxManager` protocol definition.
  - `DockerSandboxManager` adapter managing container lifecycles via Docker SDK.
  - Hardened Docker runner definition (`docker/Dockerfile.runner`, image `agentic-runner:0.1.0`).
  - Security confinement: `--network none`, `:ro` volume mount, 512MB RAM, 1.0 CPU, 100 PIDs, non-root user `uid=10001`.
  - 30.0s execution timeout enforcement with host timer `SIGKILL`.
  - Source tree SHA-256 pre/post cryptographic integrity verification.
  - `PytestRunner` telemetry capture (exit code, stdout, stderr, tracebacks).
  - `CoverageExtractor` parsing `coverage.json` for line and branch coverage deltas.
- **Required Modules / Files**:
  *(Note: To be created upon explicit human authorization)*
  - `docker/Dockerfile.runner`
  - `src/agentic_test/core/protocols/sandbox.py` (`SandboxManager` protocol; Stage 3 Listing 4.2)
  - `src/agentic_test/execution/__init__.py`
  - `src/agentic_test/execution/docker_sandbox.py` (`DockerSandboxManager`)
  - `src/agentic_test/execution/runner.py` (`PytestRunner`)
  - `src/agentic_test/execution/coverage.py` (`CoverageExtractor`)
  - `src/agentic_test/execution/service.py` (`ExecutionService`)
  - `tests/test_docker_sandbox.py`
  - `tests/test_coverage_extractor.py`
- **Dependencies on Previous Iterations**: Iteration 1.1 (models), Iteration 1.4 (`TestCandidate`, validation).
- **Required Classes / Interfaces**:
  - `SandboxConfig`, `ExecutionRawResult` (Stage 3 Listing 4.2)
  - `SandboxManager` (Protocol; Stage 3 Listing 4.2)
  - `ExecutionEvidence` (Entity; Stage 2 Section 4.3.3)
  - `DockerSandboxManager` (Concrete realization of `SandboxManager`)
  - `PytestRunner` (`construct_command`, `parse_test_outcomes`)
  - `CoverageExtractor` (`parse_coverage_json`, `calculate_deltas`)
  - `ExecutionService` (`execute`)
- **Required Tests**:
  - `tests/test_docker_sandbox.py` (Unit tests with mock Docker SDK and integration tests):
    - Confinement options verified (`network_mode="none"`, `mem_limit="512m"`, `cpu_quota=100000`, `pids_limit=100`, `user="10001:10001"`).
    - Read-only source mounting (`:ro`) verified; write attempt triggers `EROFS`.
    - Pre/post SHA-256 source hash comparison detects modified files and raises integrity alert.
    - Host-side timeout terminates long-running container at 30.0s returning exit code 124.
  - `tests/test_coverage_extractor.py`:
    - Parses valid `coverage.json` and extracts line and branch percentages.
- **Acceptance Criteria**:
  - Satisfies `FR-11` to `FR-15`: Container spawned, isolated, network-disabled, resource-capped, read-only mounted, and automatically removed.
  - Satisfies `FR-16` & `FR-17`: Execution telemetry and coverage deltas extracted.
  - Satisfies `INV-01`, `INV-02`, `NFR-03`, `NFR-08`, and `NFR-12`.
- **Definition of Done**:
  - All tests pass; Docker sandbox security and timeout mechanisms verified.
  - Cryptographic verification proves zero modification of application code.
- **What Must NOT Be Implemented Yet**:
  - Dual-engine failure diagnosis (deferred to Iteration 1.6).
  - SQLite persistence and CLI integration (deferred to Iteration 1.6).

---

### Iteration 1.6 — Dual-Engine Failure Triage, State Persistence & CLI Integration

- **Objective**: Implement failure diagnosis (deterministic rules + cognitive LLM triage across 7 canonical categories), SQLite relational persistence and event sourcing, LangGraph `StateGraph` workflow assembly, and the Typer/Rich CLI.
- **Scope**:
  - `DiagnosisService` with `DeterministicRuleClassifier` (Engine 1) and `CognitiveLLMClassifier` (Engine 2).
  - 7-category taxonomy classification; `APPLICATION_BUG` flagged for human review without auto-repair.
  - Relational SQLite schema (6 tables: `runs`, `checkpoints`, `events`, `test_candidates`, `execution_evidence`, `failure_diagnoses`).
  - LangGraph `StateGraph` compiling 7 node wrappers and conditional edges.
  - Interactive Typer CLI with Rich terminal formatting (`agentic-test run --repo <path>`).
- **Required Modules / Files**:
  *(Note: To be created upon explicit human authorization)*
  - `src/agentic_test/diagnosis/__init__.py`
  - `src/agentic_test/diagnosis/rules.py` (`DeterministicRuleClassifier`)
  - `src/agentic_test/diagnosis/llm_classifier.py` (`CognitiveLLMClassifier`)
  - `src/agentic_test/diagnosis/service.py` (`DiagnosisService`)
  - `src/agentic_test/storage/__init__.py`
  - `src/agentic_test/storage/database.py` (SQLite schema initialization, DDL; Stage 3 Listing 4.4)
  - `src/agentic_test/storage/events.py` (`SQLiteEventStore`)
  - `src/agentic_test/workflow/__init__.py`
  - `src/agentic_test/workflow/state.py` (LangGraph state wrapper)
  - `src/agentic_test/workflow/graph.py` (Compiled `StateGraph`)
  - `src/agentic_test/cli/__init__.py`
  - `src/agentic_test/cli/console.py` (Rich formatting)
  - `src/agentic_test/cli/app.py` (Typer entrypoint)
  - `tests/test_diagnosis_service.py`
  - `tests/test_storage.py`
  - `tests/test_workflow_graph.py`
  - `tests/test_cli.py`
- **Dependencies on Previous Iterations**: Iterations 1.1 through 1.5.
- **Required Classes / Interfaces**:
  - `FailureCategory` (Enum; Stage 2 Section 4.3.3)
  - `FailureDiagnosis` (Entity; Stage 2 Section 4.3.3)
  - `DeterministicRuleClassifier`, `CognitiveLLMClassifier`, `DiagnosisService`
  - `SQLiteEventStore`, SQLite connection manager
  - LangGraph `StateGraph` instance with 7 nodes and conditional edges
  - Typer CLI app
- **Required Tests**:
  - `tests/test_diagnosis_service.py`:
    - Deterministic engine resolves timeout $\rightarrow$ `ENVIRONMENT_FAILURE`, missing module $\rightarrow$ `CONFIGURATION_ERROR` with confidence 1.0 and zero LLM calls.
    - Cognitive engine disambiguates assertion error between `TEST_OUTDATED` and `APPLICATION_BUG`.
    - `APPLICATION_BUG` flagged with `is_application_bug = True` and barred from test repair.
  - `tests/test_storage.py`:
    - DDL script creates all 6 tables and secondary indices.
    - Foreign key constraints with `ON DELETE CASCADE` verified.
  - `tests/test_workflow_graph.py`:
    - LangGraph routes through thin wrappers and checkpoints state transitions.
  - `tests/test_cli.py`:
    - CLI arguments parsed and validated; non-existent repo returns exit code 2.
- **Acceptance Criteria**:
  - Satisfies `FR-18`, `FR-19`, `FR-20`: Failures classified across 7 categories; application bugs isolated.
  - Satisfies `NFR-05` (Audit Logging), `NFR-07` (Crash Recovery), `INV-04`.
  - End-to-end execution completes on benchmark codebases.
- **Definition of Done**:
  - Complete test suite passes.
  - CLI runs end-to-end and outputs structured reports.
  - All Increment 1 acceptance criteria met.
- **What Must NOT Be Implemented Yet**:
  - Increment 2 automated test maintenance / healing logic.
  - Increment 3 human-in-the-loop approval UI or PR creation.

---

## 3. Open Architectural Questions

The following implementation-relevant ambiguities and conflicts are evidenced directly by the approved Stage specifications. Each item documents the concept, exact sections, affected components, implementation impact, and blocking status.

### Question 1: Gate 3 Host-Side Collection vs. Prohibition on Direct Host Code Execution
- **Evidenced Concept**:
  - Stage 2 Section 4.3.2.4 (UC-04 Step 4) & Stage 3 Section 4.4.4.4: Gate 3 (`CollectionValidator`) writes candidate code to a temporary file and runs `pytest --collect-only` in the host environment.
  - Stage 1 Section 4.1.1, Section 4.2.4 (RSK-02), and Stage 2 Section 4.3.4 (INV-01): Untrusted LLM-generated code must never directly execute on the host machine; all dynamic execution must occur inside the hardened Docker sandbox.
- **Conflict**: Pytest collection executes module-level Python statements during import. Running `pytest --collect-only` on the host allows arbitrary untrusted LLM-generated code to execute directly on the developer workstation or CI host.
- **Affected Classes / Files**: `CollectionValidator` (`src/agentic_test/validation/gates.py`), `ValidationPipeline`, `test_validation_pipeline.py`.
- **Implementation Impact**: Running Gate 3 on the host violates `INV-01` and introduces a remote code execution vulnerability. Running Gate 3 inside Docker requires container spin-up during static validation, altering pipeline latency and design.
- **Blocking Status**: **BLOCKS Iteration 1.4 Gate 3 implementation**. Kept strictly blocked pending separate human security decision.

### Question 2: `WorkflowState.evidence` Cardinality vs. Multiple Candidates
- **Evidenced Concept**:
  - Stage 3 Section 4.4.3 defines `WorkflowState.candidates: List[TestCandidate]` and `WorkflowState.evidence: Optional[ExecutionEvidence] = None` (singular).
  - Stage 2 Section 4.3.3 Figure 4.1 & Stage 3 Section 4.4.7.1.1 define the relational relationship as `test_candidates (1) ── (0..1) execution_evidence`.
  - In SQLite schema (Stage 3 Listing 4.4), `execution_evidence` has a foreign key `candidate_id TEXT NOT NULL REFERENCES test_candidates(candidate_id)`.
- **Conflict**: When a run generates multiple test candidates across multiple affected symbols, a single `evidence: Optional[ExecutionEvidence]` field in `WorkflowState` cannot hold individual execution results for each candidate.
- **Affected Classes / Files**: `WorkflowState` (`src/agentic_test/core/state.py`), `ExecutionService`, `DiagnosisService`.
- **Implementation Impact**: Either `WorkflowState.evidence` must become a list/map (`evidence: List[ExecutionEvidence]` or `Dict[str, ExecutionEvidence]`), or `ExecutionEvidence` must represent an aggregate run evidence object with candidate-level breakdown.
- **Blocking Status**: Kept open for Iteration 1.5; **BLOCKS Iteration 1.5 multi-candidate execution modeling** pending human decision.

### Question 3: Data Used by Analysis/Planning vs. Documented `RepositorySnapshot` Fields
- **Evidenced Concept**:
  - Stage 2 Section 4.3.3 formally defines `RepositorySnapshot` attributes: `repo_path`, `current_commit`, `base_commit`, `branch_name`, `tracked_files`, `existing_test_files`, `source_tree_hash`, `is_valid`, `created_at`.
  - Stage 3 Section 4.4.3 states `ingest_and_analyze_node` populates `state.snapshot`, and `plan_execution_node` calls `ExecutionPlanner.plan(state.snapshot)`.
  - Stage 3 Section 4.4.4.2 references `snapshot.diff_hunks` and `affected_symbols` inside `ExecutionPlanner`.
  - Stage 2 Section 4.3.2.1 references `AnalysisResult.syntax_errors` and Stage 3 Section 4.4.4.1 references `snapshot.syntax_errors`.
- **Human Architectural Decision (Approved 2026-09-23)**: **RESOLVED via Option A**. The human architectural decision maker explicitly approved Option A as a limited architectural decision:
  - `diff_hunks`, `affected_symbols`, and `syntax_errors` are added to `RepositorySnapshot` as immutable, typed fields with independent empty-list defaults (`Field(default_factory=list)`).
  - This is a human-approved clarification of the Stage 2/Stage 3 discrepancy, not a claim that Stage 2 originally listed these fields. The original Stage documents remain unchanged.
  - The existing 9 documented fields, the `AnalysisService.analyze() -> RepositorySnapshot` return contract, the `ExecutionPlanner.plan(snapshot: RepositorySnapshot) -> ExecutionPlan` signature, and the `WorkflowState.snapshot` flow are preserved.
  - These three fields are **not** added separately to `WorkflowState`, and `AnalysisResult` is **not** introduced as a new public entity.
  - Note: `affected_symbols` is populated during Iteration 1.2 (AST analysis is not implemented in Iteration 1.1).
- **Blocking Status**: **RESOLVED for Iteration 1.1**.

### Question 4: Verifiable Method for Mapping Discovered Tests to Symbols
- **Evidenced Concept**:
  - Stage 2 Section 4.3.1.1 [FR-04] states: "Existing test files matching `test_*.py` or `*_test.py` are mapped to target symbols with 100% precision."
  - Stage 3 Section 4.4.4.1 lists `TestDiscovery.extract_tested_symbols(test_file: Path) -> List[str]`.
  - Stage 3 Section 4.4.4.2 Rule P3 routes to `ROUTE_TO_DOCKER_EXECUTION` if $\forall s \in \text{affected\_symbols} : s \in \text{existing\_tests}$.
- **Conflict**: Statically determining which target callable symbols are tested by existing test files without executing tests or measuring dynamic coverage cannot achieve "100% precision" in general Python code (due to dynamic imports, fixtures, mocking, and helper test assertions).
- **Affected Classes / Files**: `TestDiscovery` (`src/agentic_test/analysis/discovery.py`), `ExecutionPlanner`.
- **Implementation Impact**: The static mapping heuristic (e.g., AST call visitor, naming convention `test_<symbol>`, or import inspection) must be explicitly specified to ensure deterministic, testable behavior.
- **Blocking Status**: **BLOCKS Iteration 1.2 / 1.3 test discovery heuristic approval**.

### Question 5: Collection Immutability (Decision B) and Remote Origin (Decision A)
- **Evidenced Concept**:
  - `NFR-06` requires immutable workflow state transitions and data objects to guarantee deterministic replayability and state auditability.
  - Stage 2 Section 4.3.3 and Stage 3 Section 4.4.3 specified collection fields as `List[T]`.
  - Pydantic v2 `frozen=True` prevents reassignment of model attributes but allows in-place mutation of Python `list` objects (`.append()`, `.pop()`, `[0] = ...`).
  - Stage 2 FR-01 specified extracting remote origin URL, but `RepositorySnapshot` schema did not include `remote_origin`.
- **Human Architectural Decisions (Approved 2026-09-23)**:
  - **Decision A (Remote Origin)**: Approved Option 3.1. `GitService.get_remote_origin() -> Optional[str]` inspects only the remote named `"origin"`, returns `None` when absent (no substitution or fallback to upstream), and raises `RepositoryValidationError` on config/read failures. `RepositorySnapshot` retains strictly the 9 documented Stage 2 fields + 3 Option A fields without `remote_origin`.
  - **Decision B (Collection Immutability)**: Explicit human-approved implementation clarification. All collection fields in domain models and `WorkflowState` are converted from `List[T]` to standard immutable containers `Tuple[T, ...]`:
    - `SymbolContract.dependencies: Tuple[str, ...] = Field(default_factory=tuple)`
    - `RepositorySnapshot.tracked_files: Tuple[Path, ...] = Field(default_factory=tuple)`
    - `RepositorySnapshot.existing_test_files: Tuple[Path, ...] = Field(default_factory=tuple)`
    - `RepositorySnapshot.diff_hunks: Tuple[DiffHunk, ...] = Field(default_factory=tuple)`
    - `RepositorySnapshot.affected_symbols: Tuple[SymbolContract, ...] = Field(default_factory=tuple)`
    - `RepositorySnapshot.syntax_errors: Tuple[str, ...] = Field(default_factory=tuple)`
    - `ExecutionPlan.target_symbols: Tuple[SymbolContract, ...] = Field(default_factory=tuple)`
    - `ExecutionPlan.existing_tests_to_run: Tuple[Path, ...] = Field(default_factory=tuple)`
    - `TestCandidate.imports: Tuple[str, ...] = Field(default_factory=tuple)`
    - `WorkflowState.candidates: Tuple[TestCandidate, ...] = Field(default_factory=tuple)`
    - `WorkflowState.diagnoses: Tuple[FailureDiagnosis, ...] = Field(default_factory=tuple)`
  - Standard JSON array `[...]` serialization and round-trip validation are preserved.
  - Custom wrappers (`FrozenList`), proxies, and property systems are rejected.
- **Blocking Status**: **RESOLVED for Iteration 1.1**.

### Question 6: FR-01 "Required Configuration Files" Specification & Scope
- **Evidenced Concept**:
  - Stage 2 Section 4.3.1.1 [FR-01] Statement specifies: *"The system shall validate that a specified local directory is a valid Git repository, extract core Git metadata (current commit SHA, branch name, remote origin, and base comparison commit), and verify that required configuration files exist."*
  - In contrast, the normative Acceptance Criteria for FR-01 state: *"The system accepts valid Git repositories, extracts the HEAD commit SHA (40-character hex string), rejects non-existent or uninitialized directories with an explicit `RepositoryValidationError`, and populates `RepositorySnapshot.is_valid = True`."*
  - Stage 2 Section 4.3.2.1 (UC-01 Step 2) specifies: *"GitService validates directory existence and verifies standard Git repository layout (`.git` directory, `HEAD` reference) (FR-01)."*
  - Stage 3 Section 4.4.4.1 lists `GitService: validate_repository(path: Path) -> bool, get_head_commit() -> str, compute_diff(base_commit: str) -> List[DiffHunk]`.
- **Human Architectural Decision (Approved 2026-09-23)**: **RESOLVED via Option 1**. The human architectural decision maker explicitly approved Option 1 as an implementation clarification of the incomplete FR-01 wording for Increment 1:
  - *"Required configuration files"* means the standard Git repository configuration and structural references required to validate a usable local repository, including the `.git` configuration/metadata and `HEAD` reference.
  - Project-level Python configuration files such as `pyproject.toml`, `setup.py`, `setup.cfg`, or `tox.ini` are NOT universal required files for target repositories and must not be imposed by `GitService`.
  - Project configuration and test discovery remain later analysis concerns where specified; no new required-file policy or public model field is added.
  - The original Stage documents remain unchanged.
- **Implementation Status**: `GitService.validate_repository()` validates standard Git layout (`.git` directory, readable Git config, valid non-bare object database, and valid `HEAD` reference). Target repository project configuration files are not required for repository ingestion.
- **Blocking Status**: **RESOLVED for Increment 1**.

