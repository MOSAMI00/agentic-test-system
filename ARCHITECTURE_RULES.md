# ARCHITECTURE_RULES.md — System Architecture & Strict Engineering Rules

## 1. System Scope

The **Agentic Test Generation and Maintenance System** is an automated, agent-assisted engineering system designed to synthesize, validate, execute, and triage unit tests for Python software projects as codebases evolve.

### 1.1 In-Scope for Increment 1 (Baseline Verification Loop)
- **Target Language and Runtime**: Pure Python projects running on Python 3.11+ (CPython 3.11.8+ / 3.12.3+).
- **Target Test Framework**: `pytest` and `pytest-cov`.
- **Repository Context**: Local Git repositories inspected via read-only mechanisms (`GitPython`).
- **Dynamic Execution**: Isolated Docker container execution using a hardened base image (`agentic-runner:0.1.0`).
- **Cognitive Model Gateway**: Multi-provider LLM gateway via `LiteLLMService` pinned to deterministic settings (`temperature = 0.0` or `0.2`).
- **Storage and State**: ACID-compliant SQLite relational persistence and append-only event sourcing for state snapshots, checkpoints, test candidates, execution evidence, and failure diagnoses.
- **User Interface**: Command-line interface built with `Typer` and `Rich`.

### 1.2 Strict Scope Exclusions for Increment 1
- **Zero Production Source-Code Modification**: The system is programmatically and physically barred from mutating any file under target application source trees (e.g., `src/`).
- **Zero Automated Program Repair / Bug Fixing**: The system never modifies application code to make tests pass. Suspected application defects are flagged as `APPLICATION_BUG` and isolated for developer review.
- **Zero Automated Git Commits, Merges, or Pushes**: Generated tests reside strictly in isolated artifact runs (`.agentic_test/runs/<run_id>/candidates/`). No direct writes or commits to the host Git repository occur.
- **No Test Maintenance / Healing**: Automated updating or healing of broken existing tests is strictly deferred to **Increment 2**.
- **No Interactive Human-in-the-Loop Approval Workflows**: Interactive web/CLI patch review and PR synthesis are strictly deferred to **Increment 3**.
- **No Multi-Language Support**: Target code is strictly Python; languages such as Java, TypeScript, or Go are out of scope.
- **No Non-Unit Test Generation**: Integration, End-to-End (E2E), GUI, performance, and API tests are strictly excluded.

---

## 2. Five-Layer Architecture

The system enforces a strict 5-layer downward-dependent architecture (Stage 3 Section 4.4.1):

```text
Layer 1: Presentation Layer        (Typer CLI, Rich Terminal Formatter)
               │ (invokes execution commands)
               ▼
Layer 2: Orchestration Layer       (LangGraph StateGraph, SqliteSaver Checkpointer)
               │ (orchestrates service calls)
               ▼
Layer 3: Domain Services Layer     (Analysis, Planning, Generation, Validation, Execution, Diagnosis)
               │ (interacts via protocol contracts)
               ▼
Layer 4: Protocol Abstractions     (CodeAnalyzer, SandboxManager, LLMService)
               ▲
               │ (implements protocol contracts)
Layer 5: Infrastructure & Adapters (PythonASTAnalyzer, GitService, DockerSandboxManager, LiteLLMService, SQLite)
```

### Coupling and Dependency Inversion Rules
1. **Downwards Dependency Flow**: Dependencies flow strictly downward from Layer 1 to Layer 5. Higher layers may depend on abstractions in lower layers; lower layers must never depend on higher layers.
2. **Graph-Agnostic Domain Services**: Domain services in Layer 3 must contain **zero imports of `langgraph`** or presentation frameworks (`typer`, `rich`). Domain services must remain 100% pure Python and graph-agnostic, capable of being tested in isolation without orchestrators or Docker daemons.
3. **Protocol Boundaries**: Domain services interact with language parsing, container runtimes, and cognitive models exclusively through formal Python protocols defined in Layer 4.

---

## 3. Domain Services

Domain services in Layer 3 encapsulate all business logic, deterministic heuristics, prompt assembly, and verification pipelines:

1. **`AnalysisService`**: High-level facade coordinating Git inspection, static AST traversal, and test discovery into an immutable `RepositorySnapshot`.
2. **`TestDiscovery`**: Discovers existing test files (`test_*.py`, `*_test.py`) and maps existing test functions to target symbols.
3. **`ExecutionPlanner`**: Deterministic routing gate evaluating working tree diffs and symbol coverage to decide execution paths without invoking LLMs.
4. **`ContextAssembler`**: Assembles compact, symbol-scoped prompts containing function signatures, docstrings, type hints, dependencies, and diff hunks, enforcing token budgets ($\le 4,000$ tokens).
5. **`GenerationService`**: Orchestrates LLM prompt execution, parses structured JSON test candidates, and coordinates bounded internal candidate repair before validation.
6. **`ValidationPipeline`**: Multi-gate static validation barrier executing sequential non-regenerative gates (Syntax, Security, Collection) prior to container dispatch.
7. **`ExecutionService`**: Coordinates ephemeral container lifecycle, read-only volume mounts, pytest execution, and evidence collection.
8. **`PytestRunner`**: Formats CLI test execution arguments and parses JUnit XML / stdout / stderr execution telemetry.
9. **`CoverageExtractor`**: Parses `coverage.json` to calculate exact line and branch coverage metrics and compute coverage deltas ($\Delta Cov$).
10. **`DiagnosisService`**: Orchestrates two-tier failure triage (deterministic regex pattern matching followed by cognitive LLM reasoning).

---

## 4. Protocol Abstractions

The system isolates volatile external technologies behind three formal Python `typing.Protocol` definitions decorated with `@runtime_checkable` (Stage 3 Section 4.4.2):

### 4.1 `CodeAnalyzer` Protocol
Decouples static source parsing and symbol extraction from specific parser implementations (`NFR-11`):
```python
@runtime_checkable
class CodeAnalyzer(Protocol):
    def parse_symbols(self, file_path: Path, content: str) -> List[SymbolContract]: ...
    def extract_dependencies(self, file_path: Path, content: str) -> List[str]: ...
    def resolve_affected_symbols(self, symbols: List[SymbolContract], diff_hunks: List[DiffHunk]) -> List[SymbolContract]: ...
```

### 4.2 `SandboxManager` Protocol
Decouples container lifecycle and isolated execution from virtualization engines (`NFR-12`):
```python
@runtime_checkable
class SandboxManager(Protocol):
    def create_environment(self, config: SandboxConfig) -> str: ...
    def execute_command(self, container_id: str, command: List[str], workdir: str = "/workspace") -> ExecutionRawResult: ...
    def cleanup(self, container_id: str) -> None: ...
```

### 4.3 `LLMService` Protocol
Decouples cognitive synthesis and failure classification from specific model providers (`NFR-13`):
```python
@runtime_checkable
class LLMService(Protocol):
    def generate_structured(self, prompt: str, system_instruction: str, response_schema: Type[T], temperature: float = 0.0, max_tokens: int = 2048) -> T: ...
    def estimate_tokens(self, text: str) -> int: ...
```

---

## 5. Infrastructure Adapters

Concrete technology bindings reside in Layer 5 and implement protocol abstractions:
- **`PythonASTAnalyzer`**: Implements `CodeAnalyzer` using Python standard library `ast`. Static analysis must **never execute target application code** during parsing or symbol extraction.
- **`GitService`**: Wraps `GitPython` to extract commit metadata and unified diffs.
- **`DockerSandboxManager`**: Implements `SandboxManager` using the official Docker SDK for Python (`docker`).
- **`LiteLLMService`**: Implements `LLMService` wrapping `litellm` with exponential backoff and fallback support.
- **`SQLiteEventStore` & `SqliteSaver`**: Provides ACID persistence, state snapshots, and telemetry event storage.
- **`PydanticSettings`**: Strongly-typed configuration loaded from environment variables and `.env` files.

---

## 6. LangGraph Orchestration

Workflows are orchestrated by a compiled `LangGraph` `StateGraph` (Stage 3 Section 4.4.3).

### 6.1 Seven Node Wrappers
Nodes act as thin adapters that read from `WorkflowState`, call a domain service, and return state updates:
1. `ingest_and_analyze_node` $\rightarrow$ calls `AnalysisService.analyze()`.
2. `plan_execution_node` $\rightarrow$ calls `ExecutionPlanner.plan()`.
3. `generate_tests_node` $\rightarrow$ calls `GenerationService.generate()`.
4. `validate_candidates_node` $\rightarrow$ calls `ValidationPipeline.validate()`.
5. `execute_sandbox_node` $\rightarrow$ calls `ExecutionService.execute()`.
6. `diagnose_failure_node` $\rightarrow$ calls `DiagnosisService.diagnose()`.
7. `report_node` $\rightarrow$ formats and emits final run reports and telemetry.

### 6.2 Conditional Branch Edges
- **`route_after_planning`**:
  - `ROUTE_NO_OP` $\rightarrow$ `report_node`.
  - `ROUTE_TO_DOCKER_EXECUTION` $\rightarrow$ `execute_sandbox_node`.
  - `ROUTE_TO_TEST_GENERATION` $\rightarrow$ `generate_tests_node`.
- **`route_after_validation`**:
  - At least one candidate `PASSED` $\rightarrow$ `execute_sandbox_node`.
  - Zero candidates passed (all rejected) $\rightarrow$ `report_node` (no regeneration loop).
- **`route_after_execution`**:
  - `exit_code == 0` (all passed) $\rightarrow$ `report_node`.
  - `exit_code != 0` (failure) $\rightarrow$ `diagnose_failure_node`.

---

## 7. WorkflowState

State is managed through an immutable Pydantic v2 model:
```python
class WorkflowState(BaseModel):
    run_id: str
    repo_path: Path
    created_at: datetime = Field(default_factory=datetime.utcnow)
    snapshot: Optional[RepositorySnapshot] = None
    plan: Optional[ExecutionPlan] = None
    candidates: List[TestCandidate] = Field(default_factory=list)
    generation_retry_count: int = 0
    max_generation_retries: int = 2
    evidence: Optional[ExecutionEvidence] = None
    diagnoses: List[FailureDiagnosis] = Field(default_factory=list)
    final_status: str = "INITIALIZED"
    error_message: Optional[str] = None
```

### State Rules:
- Direct in-place mutation of `WorkflowState` attributes is prohibited. Transitions must return new state dictionaries or validated model copies.
- State is transactionally snapshotted at every graph node boundary to SQLite using `SqliteSaver`.

---

## 8. Test Generation

- **Deterministic Gating (`INV-03`)**: Cognitive test generation is invoked **only** when `ExecutionPlan.route == ROUTE_TO_TEST_GENERATION`.
- **Symbol-Specific Context**: Prompts are assembled per target symbol by `ContextAssembler`. Context includes target function code, signature, docstring, dependencies, and diff hunk, pruned to $\le 4,000$ tokens.
- **Candidate Cardinality**: A `TestCandidate` is associated with a target symbol and contains one cohesive test suite file targeting that symbol. It may contain multiple test functions and assertions; **do not reduce it to a single assertion**.
- **Structured Schema**: LLM generation enforces structured JSON output schema matching `{ "candidate_code": str, "imported_modules": list[str], "targeted_cases": list[str] }`.

---

## 9. Candidate Repair

- **Ownership**: `GenerationService` strictly owns bounded internal candidate repair.
- **Retry Budget**: Internal repair is limited to $R_{max} = 2$ retries (`max_generation_retries = 2`).
- **Applicability**: Internal candidate repair is triggered exclusively for repairable construction defects (malformed JSON, incomplete syntax, formatting anomalies) before submitting candidates to the validation pipeline.
- **Containment**: Repair logic is strictly internal to `GenerationService`. It must **never** be moved into `ValidationPipeline` or `StateGraph`. Candidates that fail internal repair after 2 attempts are flagged as `MALFORMED_OUTPUT` and quarantined before validation.

---

## 10. Validation Pipeline

- **Non-Regenerative Final Acceptance Boundary**: `ValidationPipeline` serves as an immutable acceptance gate. It must **not** call `GenerationService`, has zero callbacks to generation, and must **never** initiate a graph-level regeneration cycle.
- **Sequential Multi-Gate Verification**:
  1. **Gate 1 (`SyntaxValidator`)**: Validates Python grammar using `ast.parse`. Syntax errors immediately mark the candidate `REJECTED_SYNTAX`.
  2. **Gate 2 (`SecurityValidator`)**: AST visitor scanning for blacklisted calls (`os.system`, `subprocess`, `eval`, `exec`, socket APIs, unauthorized filesystem writes). Violations immediately mark the candidate `REJECTED_SECURITY` and permanently quarantine it. Security violations are strictly non-retryable.
  3. **Gate 3 (`CollectionValidator`)**: Dry-run pytest collection (`pytest --collect-only`) verifying import resolution and fixture existence.
- **Candidate Quarantine**: Rejected candidates are tagged with failure codes, documented with error traces, and persisted to the quarantine table.

### Mandatory Safety Conflict Notice: Gate 3 Collection vs. Host Execution Prohibition
> [!CAUTION]
> **UNRESOLVED BLOCKING ARCHITECTURAL CONFLICT: GATE 3 COLLECTION**
>
> - **Specification Text**: Stage 2 Section 4.3.2.4 (UC-04 Step 4) and Stage 3 Section 4.4.4.4 state: `CollectionValidator` executes dry-run `pytest --collect-only` in the host environment.
> - **Safety Invariant**: Stage 1 Section 4.1.1, Section 4.2.4 (RSK-02), and Stage 2 Section 4.3.4 (INV-01) strictly prohibit direct host execution of untrusted generated code, mandating total container isolation.
> - **The Conflict**: Pytest test collection imports test modules. Module-level Python code is executed during collection. Executing `pytest --collect-only` on the host runner permits arbitrary untrusted LLM-generated code to execute directly on the developer workstation or CI host before reaching the Docker sandbox.
> - **Binding Instruction**: This conflict is an **unresolved blocking architectural issue**. Implementation agents must **not** authorize or implement host-side `pytest --collect-only` execution for Gate 3, nor silently move Gate 3 inside Docker without human approval. Gate 3 implementation is blocked pending an explicit architectural decision by the human decision maker.

---

## 11. Sandbox Execution

- **Hardened Ephemeral Container**: All dynamic test execution must occur within an isolated container spawned from `agentic-runner:0.1.0`. Containers are created on demand and automatically removed (`auto_remove = True`).
- **Network Isolation (`INV-01`)**: Enforce `--network none`. Zero bytes of network traffic are permitted.
- **Resource Ceilings (`INV-01`)**:
  - Memory: `--memory 512m` (OOM kill exit code 137).
  - CPU: `--cpus 1.0`.
  - Process limit: `--pids-limit 100`.
- **Execution Timeout (`INV-01`)**: Hard wall-clock limit of 30.0 seconds monitored by a host-side timer. Container is killed with `SIGKILL` if exceeded (exit code 124).
- **Source Protection (`INV-02`)**: Target application repository is mounted strictly read-only (`:ro`). Test candidates are mounted in an isolated ephemeral scratch volume.
- **Cryptographic Verification (`INV-02`)**: Pre-execution and post-execution SHA-256 hashes of all application source files are computed and verified. Any mismatch halts execution with `PRODUCTION_SOURCE_INTEGRITY_VIOLATION`.

---

## 12. Failure Diagnosis

Dynamic test execution failures (`exit_code != 0`) are triaged using a dual-engine cascade:
- **Engine 1 (Deterministic Triage)**: Evaluates regex patterns against stderr and tracebacks. Instantly resolves timeouts (`ENVIRONMENT_FAILURE`), missing modules (`CONFIGURATION_ERROR`), and test syntax/fixture errors (`INVALID_GENERATED_TEST`) with 100% confidence and zero LLM tokens.
- **Engine 2 (Cognitive LLM Triage)**: For unresolvable failures (assertion errors, application tracebacks), prompts the cognitive model with execution evidence, target AST, and git diff.
- **Canonical 7-Category Taxonomy**:
  1. `TEST_OUTDATED`
  2. `APPLICATION_BUG`
  3. `INVALID_GENERATED_TEST`
  4. `ENVIRONMENT_FAILURE`
  5. `CONFIGURATION_ERROR`
  6. `LLM_FAILURE`
  7. `UNKNOWN`
- **Application Bug Invariant (`FR-20`)**: A failure diagnosed as `APPLICATION_BUG` is flagged for developer review and **never triggers automated test repair**.

---

## 13. Persistence

- **ACID Relational Storage**: SQLite database (`.agentic_test/history.db`) manages 6 normalized tables (Stage 3 Section 4.4.7.1):
  1. `runs`: Top-level run lifecycle metadata.
  2. `checkpoints`: JSON-serialized `WorkflowState` snapshots per graph step.
  3. `events`: Granular operational telemetry events.
  4. `test_candidates`: Synthesized candidates, validation status, retry count, quarantine reasons.
  5. `execution_evidence`: Sandbox exit codes, stdout/stderr, execution duration, and coverage metrics.
  6. `failure_diagnoses`: Triage category, confidence score, and explanation.
- **Referential Integrity**: All child tables declare `FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE`.
- **Secondary Indexing**: Indices on `run_id`, `validation_status`, `canonical_category`, and `event_type`.

---

## 14. Security and Architectural Invariants

### INV-01: Ephemeral Container & Network Isolation Boundary
Dynamic execution must occur within an isolated container with `--network none`, `--cap-drop all`, `--security-opt no-new-privileges`, non-root user `uid=10001`, timeout $\le 30.0$s, RAM $\le 512$MB, CPU $\le 1.0$, PIDs $\le 100$.

### INV-02: Production Source-Code Read-Only Protection
Application source tree must be mounted `:ro`. $\text{Hash}_{SHA256}(\text{Source}_{pre}) = \text{Hash}_{SHA256}(\text{Source}_{post})$ must hold with zero variance.

### INV-03: Deterministic Planning Precedence (Zero-LLM Gate)
LLM test generation is forbidden when diff is empty or all affected symbols are already covered by existing tests.

### INV-04: Non-Negotiable Developer-Mediated Mutation Boundary
Automated git commits, merges, pushes, or direct repository modifications are strictly prohibited. Generated candidates remain in isolated scratch directories.

---

## 15. Prohibited Changes

Agents are strictly forbidden from making the following changes:
1. **Never mutate production source code** under `src/` or target application directories.
2. **Never execute generated test code directly on the host machine**.
3. **Never invent architectural components** such as `CandidateRepairService`, `RetryManager`, `TestMaintenanceService`, or `PatchService`.
4. **Never move candidate repair retry logic** into `ValidationPipeline` or `StateGraph`.
5. **Never initiate a regeneration loop from `ValidationPipeline`**.
6. **Never import `langgraph` or presentation frameworks** into Layer 3 domain services.
7. **Never perform automated test repair on `APPLICATION_BUG`**.
8. **Never execute automated `git commit`, `git merge`, or `git push`**.
9. **Never modify public protocol signatures, domain models, or database schemas** without explicit human architectural authorization.

---

## Architectural Invariants That Must Never Be Violated

| Invariant ID | Rule Summary | Verification Method | Violation Consequence |
| :--- | :--- | :--- | :--- |
| **INV-01** | Zero network access inside test sandbox (`--network none`) | Container inspect check; socket connection test | Immediate pipeline halt |
| **INV-01** | Ephemeral container resource caps (512MB RAM, 1.0 CPU, 30s timeout) | Docker run parameters verification | Container killed; exit code 124 / 137 |
| **INV-02** | Target application mounted `:ro`; SHA-256 pre/post match | Cryptographic hash comparison | Critical integrity alert; halt |
| **INV-03** | Zero LLM calls when diff is empty or symbols are covered | Planner unit test assertions; mock call counter | Architectural defect; test failure |
| **INV-04** | Zero autonomous git commits/merges/pushes to host repository | Git status inspection; write boundary audit | Security violation; pipeline halt |
| **SEC-01** | Static analysis must never execute target application code | Pure AST parsing; no `importlib` on target | Security violation; pipeline halt |
| **SEC-02** | Validation pipeline is non-regenerative acceptance boundary | Zero calls from validation to generation | Architectural defect; test failure |
| **SEC-03** | `APPLICATION_BUG` never triggers automated test repair | State machine transition assertions | Architectural defect; test failure |
