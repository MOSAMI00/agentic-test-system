## 4.4 Stage 3: Detailed Architectural & Component Design

---

### 4.4.1 Increment 1 Layered Subsystem Architecture
To ensure clean separation of concerns, high testability, and strict decoupling from third-party frameworks, Increment 1 is structured into a five-tier layered architecture, as illustrated in **Figure 4.2**. Dependencies flow strictly downward from the presentation layer to infrastructure adapters, preventing circular coupling and eliminating orchestration framework lock-in.

```text
[ Refer to Figure 4.2: Increment 1 Subsystem Layering and Component Architecture Diagram ]
```

The five architectural layers and their technical boundaries are defined as follows:

1. **Layer 1: Presentation Layer (CLI & UI Formatting)**
   * *Components*: Typer CLI Application (`agentic_test.cli.app`), Rich Terminal Formatter (`agentic_test.cli.console`).
   * *Responsibilities*: Parses command-line invocations, flags (`--repo`, `--diff-only`, `--model`), executes input path validations, displays real-time execution progress panels, and renders ANSI-formatted tabular summaries.
   * *Coupling Rule*: Presentation components interact exclusively with the Orchestration Layer via structured execution commands. They possess zero knowledge of underlying domain services or database models.

2. **Layer 2: Orchestration Layer (LangGraph StateGraph & Checkpointer)**
   * *Components*: StateGraph Workflow Engine (`agentic_test.workflow.graph`), Workflow State Reducer (`agentic_test.workflow.state`), SQLite Checkpointer (`SqliteSaver`).
   * *Responsibilities*: Defines the directed execution graph, evaluates conditional branch edges, coordinates independent domain service invocations, checkpoints immutable state transitions transactionally, and routes control flow between thin wrapper nodes.
   * *Coupling Rule*: Orchestration nodes act as thin coordination adapters. A node function extracts required inputs from `WorkflowState`, invokes the appropriate Domain Service method, and returns a state update dictionary. **Crucial Architectural Invariant**: Domain services contain zero imports of `langgraph` or presentation frameworks; the services remain completely agnostic of the graph.

3. **Layer 3: Domain Services Layer (Pure Python Business Logic)**
   * *Components*: `AnalysisService`, `ExecutionPlanner`, `ContextAssembler`, `GenerationService`, `ValidationPipeline`, `ExecutionService`, `DiagnosisService`.
   * *Responsibilities*: Houses all core business rules, AST traversal visitors, deterministic routing heuristics, prompt context structuring, multi-gate validation algorithms, container execution monitoring, and dual-engine failure triage.
   * *Coupling Rule*: Domain services interact with external systems (the Python parser, Docker daemon, and LLM APIs) exclusively through abstract Protocol interfaces. Domain services can be instantiated and executed in standard isolated unit tests without Docker or LangGraph dependencies.

4. **Layer 4: Protocol Abstractions Layer (Formal Typing Protocols)**
   * *Components*: `CodeAnalyzer`, `SandboxManager`, `LLMService`.
   * *Responsibilities*: Defines formal interface contracts using Python’s `typing.Protocol` with `@runtime_checkable`. These protocols encapsulate the three volatile boundaries of the system: programming language parsing (`NFR-11`), container runtime technology (`NFR-12`), and cognitive model providers (`NFR-13`).
   * *Coupling Rule*: Protocols define method signatures, parameter types, return models, and exception contracts without providing concrete implementations.

5. **Layer 5: Infrastructure & Adapters Layer (Concrete Technology Bindings)**
   * *Components*: `PythonASTAnalyzer`, `GitService` (GitPython), `DockerSandboxManager` (Docker SDK for Python), `LiteLLMService` (LiteLLM SDK), `SQLiteEventStore`, `PydanticSettings`.
   * *Responsibilities*: Implements protocol contracts by wrapping underlying libraries, executing system calls, managing Docker socket connections, handling remote HTTP/API communication, and performing ACID-compliant database writes.
   * *Coupling Rule*: Adapters depend upward on Protocol interfaces and domain data models, but remain decoupled from domain service business logic.

---

### 4.4.2 Protocol Abstractions & Formal Interface Contracts
Increment 1 strictly enforces protocol-driven development. In accordance with architectural safety principles, protocols are defined for exactly three external touchpoints that are either technology-volatile or require mock substitution during automated testing. Listings 4.1, 4.2, and 4.3 present the formal Python protocol definitions.

#### 4.4.2.1 CodeAnalyzer Protocol
The `CodeAnalyzer` protocol decouples the domain analysis service from specific language parsing implementations, satisfying **NFR-11 (Modular Language Boundaries)** and enabling future support for Tree-sitter or additional compiled languages without altering domain logic.

```python
# Listing 4.1: CodeAnalyzer Protocol Definition (Python typing.Protocol)
from pathlib import Path
from typing import List, Protocol, runtime_checkable
from agentic_test.core.models import SymbolContract, DiffHunk

@runtime_checkable
class CodeAnalyzer(Protocol):
    """
    Formal protocol decoupling static source-code parsing and symbol extraction
    from underlying parser implementations (Python ast, Tree-sitter, etc.).
    """

    def parse_symbols(self, file_path: Path, content: str) -> List[SymbolContract]:
        """
        Statically parses source code content into structured SymbolContract entities.
        Must operate deterministically and NEVER execute the parsed code.

        :param file_path: Absolute or relative path to the source file.
        :param content: Raw UTF-8 source code string.
        :return: List of parsed callable symbols (functions, methods, classes).
        :raises SyntaxParsingError: If source contains unrecoverable syntax errors.
        """
        ...

    def extract_dependencies(self, file_path: Path, content: str) -> List[str]:
        """
        Extracts imported module dependencies, symbols, and standard libraries.

        :param file_path: Path to the target source file.
        :param content: Raw source code string.
        :return: List of imported module names (e.g., ['math', 'typing', 'pydantic']).
        """
        ...

    def resolve_affected_symbols(
        self,
        symbols: List[SymbolContract],
        diff_hunks: List[DiffHunk]
    ) -> List[SymbolContract]:
        """
        Intersects unified diff line ranges with AST symbol spans to identify
        all callable symbols modified or introduced by the working tree diff.

        :param symbols: Full list of extracted symbols for the repository.
        :param diff_hunks: Parsed unified diff hunks.
        :return: Subset of symbols directly intersected by modified lines.
        """
        ...
```

#### 4.4.2.2 SandboxManager Protocol
The `SandboxManager` protocol encapsulates dynamic test execution mechanics, decoupling `ExecutionService` from container technology and satisfying **NFR-12 (Container Technology Decoupling)**. This allows the system to transition between Docker, Podman, microVMs, or in-memory mock runtimes seamlessly.

```python
# Listing 4.2: SandboxManager Protocol Definition (Python typing.Protocol)
from pathlib import Path
from typing import Dict, List, Optional, Protocol, runtime_checkable
from pydantic import BaseModel, Field

class SandboxConfig(BaseModel):
    image_tag: str = "agentic-runner:0.1.0"
    timeout_sec: float = 30.0
    memory_limit: str = "512m"
    cpu_quota: float = 1.0
    pids_limit: int = 100
    network_disabled: bool = True
    read_only_mounts: Dict[Path, str] = Field(default_factory=dict)
    read_write_mounts: Dict[Path, str] = Field(default_factory=dict)
    environment_vars: Dict[str, str] = Field(default_factory=dict)

class ExecutionRawResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool
    oom_killed: bool

@runtime_checkable
class SandboxManager(Protocol):
    """
    Formal protocol managing isolated ephemeral container sandboxes.
    Enforces security confinement, network disallowance, and resource boundaries.
    """

    def create_environment(self, config: SandboxConfig) -> str:
        """
        Provisions an isolated container sandbox adhering strictly to SandboxConfig.

        :param config: Sandbox confinement and mounting configuration.
        :return: Unique container identifier string.
        :raises SandboxInitializationError: If daemon fails to spawn container.
        """
        ...

    def execute_command(
        self,
        container_id: str,
        command: List[str],
        workdir: str = "/workspace"
    ) -> ExecutionRawResult:
        """
        Executes a command inside the container under enforced timeout limits.

        :param container_id: Active container identifier.
        :param command: Command tokens (e.g., ['pytest', 'tests/']).
        :param workdir: Execution directory within container.
        :return: ExecutionRawResult containing exit codes and captured telemetry.
        """
        ...

    def cleanup(self, container_id: str) -> None:
        """
        Forcibly destroys and removes the container and associated ephemeral mounts.

        :param container_id: Identifier of container to terminate.
        """
        ...
```

#### 4.4.2.3 LLMService Protocol
The `LLMService` protocol abstracts access to Large Language Models, satisfying **NFR-13 (LLM Provider Independence)**. It enables switching between OpenAI, Anthropic, or local open-source models via the `LiteLLMService` adapter with zero modification to generation business logic.

```python
# Listing 4.3: LLMService Protocol Definition (Python typing.Protocol)
from typing import Optional, Protocol, Type, TypeVar, runtime_checkable
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

@runtime_checkable
class LLMService(Protocol):
    """
    Formal protocol providing cognitive synthesis and classification services.
    Enforces structured output parsing, token estimation, and timeout handling.
    """

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        response_schema: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2048
    ) -> T:
        """
        Transmits prompt to cognitive model and parses response into validated schema.

        :param prompt: Formatted contextual prompt.
        :param system_instruction: Guiding behavioral system prompt.
        :param response_schema: Pydantic model enforcing JSON schema compliance.
        :param temperature: Sampling temperature (0.0 for deterministic code).
        :param max_tokens: Maximum completion token ceiling.
        :return: Validated Pydantic instance of response_schema.
        :raises LLMCommunicationError: On network, rate limit, or provider errors.
        :raises SchemaValidationError: If response violates expected schema.
        """
        ...

    def estimate_tokens(self, text: str) -> int:
        """
        Computes estimated token count for text using target model tokenizers.

        :param text: Text string to evaluate.
        :return: Non-negative integer token count.
        """
        ...
```

---

### 4.4.3 LangGraph Orchestrator & Workflow State Machine Design
Orchestration of Increment 1 is executed by a compiled `LangGraph` StateGraph. Execution state is modeled as an immutable `WorkflowState` container, with state transitions serialized transactionally to an SQLite checkpointer. **Figure 4.3** illustrates the State Machine and Node Transition Diagram.

```text
[ Refer to Figure 4.3: Increment 1 LangGraph State Machine and Node Transition Diagram ]
```

#### 1. WorkflowState Schema Specification
The unified workflow state model encapsulates all inputs, intermediate artifacts, and outputs across the lifecycle:

```python
class WorkflowState(BaseModel):
    # --- Run Identification & Execution Context ---
    run_id: str
    repo_path: Path
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # --- Populated during Ingestion & Static Analysis (UC-01) ---
    snapshot: Optional[RepositorySnapshot] = None

    # --- Populated during Deterministic Planning (UC-02) ---
    plan: Optional[ExecutionPlan] = None

    # --- Populated during Test Generation & Multi-Gate Validation (UC-03, UC-04) ---
    candidates: List[TestCandidate] = Field(default_factory=list)
    # Bounded candidate-generation / internal repair retry budget before validation
    generation_retry_count: int = 0
    max_generation_retries: int = 2

    # --- Populated during Sandbox Execution (UC-05) ---
    evidence: Optional[ExecutionEvidence] = None

    # --- Populated during Failure Diagnosis (UC-06) ---
    diagnoses: List[FailureDiagnosis] = Field(default_factory=list)

    # --- Terminal Execution Status ---
    final_status: str = "INITIALIZED"
    error_message: Optional[str] = None
```

#### 2. State Graph Node Definitions
The StateGraph comprises seven distinct nodes acting as thin service wrappers:
1. `ingest_and_analyze_node`: Invokes `AnalysisService.analyze(state.repo_path)`. Populates `state.snapshot`.
2. `plan_execution_node`: Invokes `ExecutionPlanner.plan(state.snapshot)`. Populates `state.plan`.
3. `generate_tests_node`: Invokes `GenerationService.generate(state.plan)`. If candidate code exhibits repairable construction defects (malformed JSON, incomplete syntax, formatting issues), `GenerationService` coordinates bounded internal candidate repair (`regenerate_with_feedback`) up to `max_generation_retries`. Appends finalized candidates to `state.candidates` with status `PENDING_VALIDATION`.
4. `validate_candidates_node`: Serves as a final, non-regenerative execution boundary. Coordinates sequential gate verification by invoking `ValidationPipeline.validate(candidate)` for each candidate. Flags each candidate as `PASSED` (staged for sandbox) or `REJECTED` (quarantined with gate failure code). Zero regeneration cycles are initiated.
5. `execute_sandbox_node`: Invokes `ExecutionService.execute(state.plan, validated_candidates)`. Populates `state.evidence`.
6. `diagnose_failure_node`: Invokes `DiagnosisService.diagnose(state.evidence, candidate)`. Populates `state.diagnoses`.
7. `report_node`: Compiles execution evidence, coverage deltas, and diagnostic logs into final JSON and Markdown reports. Emits completion telemetry.

#### 3. Conditional Edge Decision Logic
* **Router Edge: `route_after_planning`**:
  * If `plan.route == ROUTE_NO_OP` $\rightarrow$ Transition directly to `report_node` (no changes to process).
  * If `plan.route == ROUTE_TO_DOCKER_EXECUTION` $\rightarrow$ Transition to `execute_sandbox_node` (existing tests cover diff).
  * If `plan.route == ROUTE_TO_TEST_GENERATION` $\rightarrow$ Transition to `generate_tests_node`.
* **Validation Outcome Edge: `route_after_validation`**:
  * If at least one candidate has `validation_status == PASSED` $\rightarrow$ Transition to `execute_sandbox_node`.
  * If zero candidates passed (all candidates rejected by multi-gate screening) $\rightarrow$ Transition directly to `report_node` (all defective candidates isolated in quarantine; no graph-level retry loop).
* **Execution Outcome Edge: `route_after_execution`**:
  * If `evidence.exit_code == 0` (all tests passed) $\rightarrow$ Transition to `report_node`.
  * If `evidence.exit_code != 0` (test failure or runtime error) $\rightarrow$ Transition to `diagnose_failure_node`.

---

### 4.4.4 Detailed Subsystem & Component Specifications
Following the standard four-part specification model, this section details the internal architecture, classes, algorithms, and invariants across the six domain subsystems.

#### 4.4.4.1 Repository Ingestion & Static Analysis Subsystem
1. **Core Responsibilities**: Validates local Git repositories, parses unified diffs against baseline commits, statically traverses Python ASTs to extract callable symbols, and discovers existing pytest test cases without dynamic code execution.
2. **Component Classes & Method Signatures**:
   * `GitService`: `validate_repository(path: Path) -> bool`, `get_head_commit() -> str`, `compute_diff(base_commit: str) -> List[DiffHunk]`.
   * `PythonASTAnalyzer`: Implements `CodeAnalyzer` protocol (`parse_symbols`, `extract_dependencies`, `resolve_affected_symbols`).
   * `TestDiscovery`: `discover_test_suites(repo_path: Path) -> List[Path]`, `extract_tested_symbols(test_file: Path) -> List[str]`.
   * `AnalysisService`: High-level facade coordinating git, ast, and discovery services into an immutable `RepositorySnapshot`.
3. **Internal Algorithms & Decision Logic**:
   * *AST Symbol Visitor*: Inherits from `ast.NodeVisitor`. Traverses `ast.FunctionDef`, `ast.AsyncFunctionDef`, and `ast.ClassDef`. Computes qualified names (`ClassName.method_name`), extracts line spans $[lineno, end\_lineno]$, collects type hints from `args` and `returns`, and parses docstring literals via `ast.get_docstring()`.
   * *Diff Intersection Algorithm*: For each `DiffHunk` spanning $[new\_start, new\_start + new\_lines]$, evaluates all parsed `SymbolContract` objects. A symbol is marked `is_affected = True` if $[start\_line, end\_line] \cap [new\_start, new\_start + new\_lines] \ne \emptyset$.
4. **Error Handling & Invariant Enforcement**:
   * Statically parsing malformed Python files catches `SyntaxError`, logging an exception to `snapshot.syntax_errors` without terminating execution on other valid files.
   * `INV-02` is enforced: Analysis opens source files in binary read mode (`rb`); zero file write permissions are requested.

#### 4.4.4.2 Deterministic Planning & Context Assembly Subsystem
1. **Core Responsibilities**: Evaluates working tree diffs and symbol coverage deterministically, selects the execution route without calling LLMs, prioritizes target symbols, and bounds generation prompts within token budgets.
2. **Component Classes & Method Signatures**:
   * `ExecutionPlanner`: `plan(snapshot: RepositorySnapshot) -> ExecutionPlan`.
   * `ContextAssembler`: `assemble_prompt(symbol: SymbolContract, diff: Optional[DiffHunk], dependencies: List[str]) -> str`.
3. **Internal Algorithms & Decision Logic**:
   * *Precedence Decision Table*:
     * Rule P1: If `len(snapshot.diff_hunks) == 0` $\rightarrow$ Route: `ROUTE_NO_OP` (Rationale: Clean tree).
     * Rule P2: If all affected files are non-code (`*.md`, `*.txt`, `*.yaml`) $\rightarrow$ Route: `ROUTE_NO_OP`.
     * Rule P3: If `affected_symbols` is non-empty AND $\forall s \in \text{affected\_symbols} : s \in \text{existing\_tests}$ $\rightarrow$ Route: `ROUTE_TO_DOCKER_EXECUTION`.
     * Rule P4: If $\exists s \in \text{affected\_symbols} : s \notin \text{existing\_tests}$ $\rightarrow$ Route: `ROUTE_TO_TEST_GENERATION`.
   * *Token Pruning Algorithm*: If total context tokens exceed 4,000, context assembler truncates dependency signatures first, retaining target symbol code and diff hunk intact.
4. **Error Handling & Invariant Enforcement**:
   * Enforces `INV-03 (Zero-LLM Gate)`: Mathematical verification ensures `ROUTE_TO_TEST_GENERATION` is selected if and only if Rule P4 matches.

#### 4.4.4.3 Cognitive Test Generation Subsystem
1. **Core Responsibilities**: Formats structured prompts, invokes the cognitive model via `LiteLLMService`, parses structured JSON completions, performs bounded internal candidate repair for repairable generation defects (malformed structured output, incomplete candidate code, formatting defects), and instantiates validated-ready test candidate entities.
2. **Component Classes & Method Signatures**:
   * `GenerationService`: `generate(plan: ExecutionPlan) -> List[TestCandidate]`, `regenerate_with_feedback(symbol: SymbolContract, error_trace: str) -> TestCandidate`.
3. **Internal Algorithms & Decision Logic**:
   * *Prompt Engineering Architecture*: System instruction sets model role as an elite Python QA architect. Prompt injects: (a) target function source, (b) docstrings & types, (c) import context, (d) diff modification intent, (e) strict instruction to output pure `pytest` functions with assertions.
   * *Structured Schema Enforcement*: Uses JSON schema response constraints (`response_format={"type": "json_object"}`) requiring `{ "candidate_code": str, "imported_modules": list[str], "targeted_cases": list[str] }`.
   * *Internal Candidate Repair Loop*: If model response is malformed, truncated, or exhibits obvious repairable syntax defects, `GenerationService` triggers internal candidate repair via `regenerate_with_feedback(symbol, error_trace)` up to `max_generation_retries = 2`. The repair loop is strictly contained within generation prior to submitting candidates to `ValidationPipeline`.
4. **Error Handling & Invariant Enforcement**:
   * Non-JSON completions trigger fallback regex extractors, followed by internal candidate repair. If unrecoverable after 2 retries, candidate is marked `MALFORMED_OUTPUT` and excluded from validation.
   * API rate limits trigger exponential backoff ($2^k \times 1.5\,\text{s}$).

#### 4.4.4.4 Multi-Gate Candidate Validation Pipeline Subsystem
1. **Core Responsibilities**: Subjects synthesized candidates to a final, rigorous pre-execution static screening, serving as an immutable acceptance/rejection boundary that blocks invalid syntax, security exploits, and unresolvable fixtures prior to container dispatch. The pipeline performs pure verification and initiates zero regeneration cycles.
2. **Component Classes & Method Signatures**:
   * `BaseValidator`: Abstract validator class specifying `{abstract} +validate(candidate: TestCandidate) -> ValidationResult` and `gate_id: str`.
   * `SyntaxValidator` (Gate 1): Specializes `BaseValidator` via inheritance (`--|>`), validating Python grammar via `ast.parse` (`gate_id = "GATE_1_SYNTAX"`, `validate(candidate: TestCandidate) -> ValidationResult`).
   * `SecurityValidator` (Gate 2): Specializes `BaseValidator` via inheritance (`--|>`), scanning AST for dangerous function calls (`gate_id = "GATE_2_SECURITY"`, `validate(candidate: TestCandidate) -> ValidationResult`).
   * `CollectionValidator` (Gate 3): Specializes `BaseValidator` via inheritance (`--|>`), executing dry-run `pytest --collect-only` in host environment (`gate_id = "GATE_3_COLLECTION"`, `validate(candidate: TestCandidate) -> ValidationResult`).
   * `ValidationPipeline`: Manages the collection of `BaseValidator` instances via aggregation (`o-- 1..*`), coordinating sequential gate execution (`validate(candidate: TestCandidate) -> ValidationResult`).
3. **Internal Algorithms & Decision Logic**:
   * Sequential short-circuit: If Gate 1 fails, Gates 2 and 3 are skipped; candidate is tagged `REJECTED_SYNTAX` and quarantined.
   * Gate 2 AST Visitor inspects all `ast.Call` nodes: checks function names against security blacklist. Any match immediately marks candidate `REJECTED_SECURITY` and permanently quarantines it.
   * Gate 3 executes host-level dry-run collection. Missing fixtures or unresolved imports mark candidate `REJECTED_COLLECTION`.
   * **Zero Regeneration Invariant**: The pipeline evaluates candidates strictly for execution readiness and possesses zero dependencies on or callbacks to `GenerationService`. All gate failures terminate candidate processing into quarantine without regeneration.
4. **Error Handling & Invariant Enforcement**:
   * All gate failures are non-retryable at the validation boundary. Security violations are permanently quarantined to prevent prompt injection loops; syntax and collection failures are quarantined with zero regeneration loops.

#### 4.4.4.5 Ephemeral Container Sandbox Execution Subsystem
1. **Core Responsibilities**: Spawns isolated Docker containers, configures network confinement, enforces resource limits, bind-mounts application sources read-only, executes `pytest`, and parses coverage.
2. **Component Classes & Method Signatures**:
   * `DockerSandboxManager`: Implements `SandboxManager` protocol using official Docker SDK (`create_environment`, `execute_command`, `cleanup`).
   * `PytestRunner`: Constructs execution commands and parses test session outcomes (`construct_command(tests_path: Path, cov_source: Path) -> List[str]`, `parse_test_outcomes(stdout: str, stderr: str, exit_code: int) -> TestRunSummary`).
   * `CoverageExtractor`: Parses JSON coverage reports and calculates line/branch deltas (`parse_coverage_json(json_path: Path) -> CoverageMetrics`, `calculate_deltas(pre_cov: float, post_cov: float) -> float`).
   * `ExecutionService`: High-level facade coordinating container sandbox execution and evidence capture (`execute(plan: ExecutionPlan, candidates: List[TestCandidate]) -> ExecutionEvidence`).
3. **Internal Algorithms & Decision Logic**:
   * *Volume Isolation Strategy*: Target application directory mounted at `/workspace/src:ro`. Validated test candidates written to a temporary host directory mounted at `/workspace/tests:ro`. Scratch output directory mounted at `/workspace/output:rw`.
   * *Timeout Guard*: Runs container execution in a separate thread monitored by a Python `threading.Timer(30.0)`. If timer fires, invokes `client.containers.get(id).kill()`.
4. **Error Handling & Invariant Enforcement**:
   * Enforces `INV-01` (`--network none`, `--memory 512m`, `--cpus 1.0`, `--pids-limit 100`) and `INV-02` (pre/post SHA-256 comparison).

#### 4.4.4.6 Dual-Engine Failure Triage & Diagnosis Subsystem
1. **Core Responsibilities**: Captures failing execution evidence and disambiguates root causes across the canonical 7-category taxonomy using deterministic regex patterns first, followed by cognitive LLM reasoning for ambiguous failures.
2. **Component Classes & Method Signatures**:
   * `DeterministicRuleClassifier`: Evaluates regex patterns against stderr/tracebacks (`classify(evidence: ExecutionEvidence) -> Optional[FailureCategory]`).
   * `CognitiveLLMClassifier`: Assembles diagnosis context and queries LLM for semantic triage (`classify(evidence: ExecutionEvidence, diff: DiffHunk, symbol: SymbolContract) -> FailureDiagnosis`).
   * `DiagnosisService`: Orchestrates the dual-engine triage cascade (`diagnose(evidence: ExecutionEvidence, candidate: TestCandidate) -> FailureDiagnosis`).
3. **Internal Algorithms & Decision Logic**:
   * *Cascade Strategy*:
     1. Evaluate `DeterministicRuleClassifier`. If regex matches (e.g., `timed_out == True` $\rightarrow$ `ENVIRONMENT_FAILURE`; `ModuleNotFoundError` $\rightarrow$ `CONFIGURATION_ERROR`), assign category with confidence `1.0` and bypass Engine 2.
     2. If no rule matches, invoke `CognitiveLLMClassifier` passing failing assertion, application source, and diff hunk.
     3. Model classifies failure into `TEST_OUTDATED`, `APPLICATION_BUG`, or `INVALID_GENERATED_TEST`.
4. **Error Handling & Invariant Enforcement**:
   * Enforces `INV-04` and `FR-20`: Failures classified as `APPLICATION_BUG` are flagged for human developer review and barred from automated test maintenance loops.

---

### 4.4.5 Detailed UML Class Diagram for Increment 1
**Figure 4.4** presents the comprehensive UML Class Diagram for Increment 1, illustrating class interfaces, attributes, protocol realizations (`..|>`), inheritance hierarchies (`--|>`), associations, and dependencies across all five architectural layers.

```text
[ Refer to Figure 4.4: Increment 1 Detailed UML Class Diagram ]
```

#### Key Class-Level Architectural Characteristics in Figure 4.4:
1. **Full 5-Layer Representation without Architectural Box Pollution**:
   * *Presentation*: `CLIApp` invokes `StateGraphEngine` and formats terminal output via `RichFormatter`.
   * *Orchestration*: `StateGraphEngine` encapsulates graph node handlers, transitions immutable `WorkflowState` snapshots, persists telemetry via `SQLiteEventStore`, and commits transactional checkpoints via `SqliteSaver`.
   * *Domain Business Services*: Domain services (`AnalysisService`, `ExecutionPlanner`, `ContextAssembler`, `GenerationService`, `ValidationPipeline`, `ExecutionService`, `DiagnosisService`, `TestDiscovery`, `PytestRunner`, `CoverageExtractor`) reside as pure Python classes without presentation or graph framework coupling.
   * *Protocol Abstractions*: Exactly three formal Python protocols (`CodeAnalyzer`, `SandboxManager`, `LLMService`) decouple domain business rules from external technology implementations.
   * *Infrastructure Adapters & Storage*: Concrete adapters (`GitService`, `PythonASTAnalyzer`, `DockerSandboxManager`, `LiteLLMService`, `SQLiteEventStore`, `PydanticSettings`) provide physical realization of protocols and system configuration.
2. **Multi-Gate Validation Subsystem**:
   * `SyntaxValidator`, `SecurityValidator`, and `CollectionValidator` specialize `BaseValidator` via formal UML inheritance (`--|>`), each overriding `+validate(candidate: TestCandidate): ValidationResult`.
   * `ValidationPipeline` manages the collection of validators via aggregation (`o-- 1..*`), exposing `+validate(candidate: TestCandidate): ValidationResult`.
3. **Dual-Engine Failure Diagnosis Cascade**:
   * `DiagnosisService` maintains collaborating instances of both `DeterministicRuleClassifier` (Engine 1) and `CognitiveLLMClassifier` (Engine 2).
   * Engine 1 evaluates regex signatures first; ambiguous tracebacks are delegated to Engine 2, which prompts the cognitive model via `LLMService`.
4. **Decoupled Workflow Orchestration**:
   * `GenerationService` does not depend on or coordinate `ValidationPipeline`. Both are independently coordinated by `StateGraphEngine` in the orchestration layer.
   * Domain services depend strictly on protocol abstractions (`CodeAnalyzer`, `SandboxManager`, `LLMService`) rather than concrete container or LLM infrastructure classes.

---

### 4.4.6 Detailed UML Sequence Diagrams for Increment 1
To specify dynamic runtime interactions across subsystems, this section details three canonical sequence flows.

#### 1. SD-1.1: End-to-End Successful Test Generation Flow
**Figure 4.5** illustrates the nominal "happy path" workflow:
1. Developer invokes CLI; CLI passes `repo_path` to LangGraph orchestrator.
2. `AnalysisService` computes working tree diff and parses ASTs, returning `RepositorySnapshot`.
3. `ExecutionPlanner` selects `ROUTE_TO_TEST_GENERATION`.
4. `GenerationService` calls `LiteLLMService` to synthesize unit test candidate.
5. `ValidationPipeline` verifies syntax (Gate 1), security (Gate 2), and collection (Gate 3); all gates pass.
6. `ExecutionService` hashes source files, runs tests inside isolated Docker sandbox with `--network none`, captures exit code `0`, verifies matching post-run hashes, and extracts coverage increase.
7. Orchestrator compiles reports and displays ANSI Rich summary to developer.

```text
[ Refer to Figure 4.5: Sequence Diagram SD-1.1: End-to-End Successful Test Generation Flow ]
```

#### 2. SD-1.2: Internal Candidate Repair and Multi-Gate Validation Failure Flow
**Figure 4.6** specifies the pre-validation internal candidate repair loop and the final non-regenerative multi-gate validation failure handling:
1. `GenerationService` prompts `LiteLLMService` to synthesize a test candidate; the initial response exhibits a repairable formatting or indentation defect.
2. `GenerationService` evaluates candidate construction quality, detects the repairable syntax fault, and initiates internal candidate repair via `regenerate_with_feedback(symbol, error_trace)` (bounded by $R_{max}=2$).
3. The model returns a structurally repaired candidate (`candidate_v2`), which `GenerationService` finalizes and delivers to the orchestrator with status `PENDING_VALIDATION`.
4. Orchestrator submits the repaired candidate to `ValidationPipeline.validate(candidate_v2)`.
5. Gate 1 (`ast.parse`) executes and passes successfully.
6. Gate 2 (Bandit security AST visitor) scans the AST and detects a prohibited call (e.g., `os.system`).
7. `ValidationPipeline` immediately marks the candidate as `REJECTED_SECURITY` and halts further pipeline gates, returning the failure result to the orchestrator.
8. Orchestrator commits the rejected candidate directly to the SQLite quarantine table (`record_permanent_quarantine`). In accordance with architectural safety rules, security violations are strictly non-retryable and the validation pipeline initiates zero downstream regeneration loops.
9. With zero valid candidates remaining, the orchestrator commits checkpoint `VALIDATION_FAILED` and transitions directly to `report_node`, safely concluding execution without container dispatch.

```text
[ Refer to Figure 4.6: Sequence Diagram SD-1.2: Multi-Gate Validation Failure & Candidate Quarantine Flow ]
```

#### 3. SD-1.3: Sandbox Execution Failure & Dual-Engine Diagnosis Flow
**Figure 4.7** illustrates sandbox failure capture and dual-engine triage:
1. `ExecutionService` runs tests in Docker sandbox; test fails with assertion failure (`exit_code = 1`).
2. Evidence captured and routed to `DiagnosisService`.
3. Engine 1 (`DeterministicRuleClassifier`) evaluates traceback. No regex rule matches (ambiguous assertion).
4. Engine 2 (`CognitiveLLMClassifier`) assembles prompt with failing traceback and production diff.
5. Cognitive model identifies production logic defect, returning `APPLICATION_BUG` with confidence `0.88`.
6. System logs warning to developer, isolates evidence into bug report, and halts workflow without altering tests.

```text
[ Refer to Figure 4.7: Sequence Diagram SD-1.3: Sandbox Execution Failure & Dual-Engine Diagnosis Flow ]
```

---

### 4.4.7 Data Contracts, JSON Schemas, and State Persistence Model
To guarantee transparent auditability, crash recovery, and glass-box telemetry, Increment 1 implements an ACID-compliant relational persistence model backed by SQLite, paired with JSON Schema serialization contracts.

#### 4.4.7.1 SQLite Relational Database DDL & Physical Data Model (Figure 4.9)
Listing 4.4 documents the relational schema definitions for runs, checkpoints, events, candidates, evidence, and failure diagnoses. **Figure 4.9** presents the corresponding Relational Persistence Entity-Relationship Diagram (Physical Data Model), illustrating table structures, primary keys, foreign key referential integrity with cascading deletes, secondary indexing, and exact Crow's Foot cardinalities.

```text
[ Refer to Figure 4.9: Increment 1 Relational Persistence Entity-Relationship Diagram (Physical Data Model) ]
```

##### 4.4.7.1.1 Relational Entity Structure & Cardinality Rationale
The relational persistence model enforces auditability, state reconstruction, and telemetry logging through six normalized tables organized into two cooperating pillars:
1. **Telemetry & Checkpoint Subsystem**:
   * `runs (1) ──< (0..*) checkpoints`: Every workflow execution records an initial state snapshot upon startup and appends an immutable JSON-serialized `WorkflowState` snapshot at every graph node boundary (`step_index`), guaranteeing deterministic replayability and crash recovery.
   * `runs (1) ──< (0..*) events`: Provides fine-grained observability by persisting timestamped lifecycle events (`event_type`, `node_name`, JSON payload) emitted throughout execution.
2. **Domain Candidate Lifecycle Subsystem**:
   * `runs (1) ──< (0..*) test_candidates`: Each run originates zero or more generated test candidates targeted to affected symbols. Tracks the test source code, generation attempts, quarantine reasons, and validation status (`PENDING`, `VALIDATED`, `QUARANTINED`).
   * `test_candidates (1) ── (0..1) execution_evidence`: Implements strict conditional execution. Only candidates successfully passing the multi-gate validation pipeline are scheduled for sandbox execution. Quarantined candidates yield zero execution evidence ($1 : 0..1$). Validated candidates yield exactly one sandbox execution record capturing stdout, stderr, process exit code, execution duration, and code coverage metrics.
   * `execution_evidence (1) ── (0..1) failure_diagnoses`: Test executions that terminate successfully (`exit_code == 0`) require no diagnosis ($1 : 0$). Executions that fail (`exit_code != 0`) trigger dual-tier automated triage (deterministic pattern matching followed by cognitive LLM analysis), producing exactly one failure diagnosis record documenting the root-cause category, triage confidence score, and remediation explanation ($1 : 0..1$).

##### 4.4.7.1.2 Referential Integrity & Indexing Strategy
* **Cascade Deletion (`ON DELETE CASCADE`)**: To enforce relational hygiene and prevent orphaned audit artifacts, all child tables declare foreign key constraints on `run_id` referencing `runs(run_id)` with `ON DELETE CASCADE`. Purging or archiving a parent run cleanly cascades across checkpoints, telemetry events, test candidates, execution evidence, and failure diagnoses.
* **Secondary Indexing Optimization**: High-frequency query paths identified in non-functional requirements (`NFR-03`, `NFR-05`) are accelerated through secondary indices:
  - `idx_checkpoints_run_id`: Accelerates time-travel state recovery and resume operations.
  - `idx_events_run_id_type`: Enables rapid filtering of run telemetry streams by event category.
  - `idx_candidates_run_status`: Optimizes pipeline queries selecting pending or validated candidates.
  - `idx_evidence_run_id`: Speeds up aggregated metric compilation and execution report rendering.
  - `idx_diagnoses_run_cat`: Accelerates failure distribution analytics and regression tracking.

```sql
-- Listing 4.4: SQLite Database Schema: Workflow Events & Checkpoint Tables

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    current_commit TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    route_selected TEXT,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    final_status TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    node_name TEXT NOT NULL,
    state_payload TEXT NOT NULL,  -- JSON serialized WorkflowState snapshot
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    node_name TEXT,
    payload TEXT NOT NULL,        -- JSON event telemetry data
    emitted_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS test_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    target_symbol TEXT NOT NULL,
    test_file_path TEXT NOT NULL,
    candidate_code TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    quarantine_reason TEXT,
    retry_count INTEGER DEFAULT 0, -- Internal candidate repair attempts (0 to 2)
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS execution_evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    stdout TEXT,
    stderr TEXT,
    duration_sec REAL NOT NULL,
    timed_out BOOLEAN NOT NULL DEFAULT 0,
    line_coverage REAL,
    branch_coverage REAL,
    traceback TEXT,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES test_candidates(candidate_id)
);

CREATE TABLE IF NOT EXISTS failure_diagnoses (
    diagnosis_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    canonical_category TEXT NOT NULL,
    confidence REAL NOT NULL,
    triage_engine TEXT NOT NULL,
    explanation TEXT NOT NULL,
    is_application_bug BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id) REFERENCES execution_evidence(evidence_id)
);

-- Indexing for high-performance querying and audit reporting
CREATE INDEX IF NOT EXISTS idx_checkpoints_run_id ON checkpoints(run_id);
CREATE INDEX IF NOT EXISTS idx_events_run_id_type ON events(run_id, event_type);
CREATE INDEX IF NOT EXISTS idx_candidates_run_status ON test_candidates(run_id, validation_status);
CREATE INDEX IF NOT EXISTS idx_evidence_run_id ON execution_evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_diagnoses_run_cat ON failure_diagnoses(run_id, canonical_category);
```

#### 4.4.7.2 JSON Schema Contract: TestCandidate
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TestCandidateContract",
  "type": "object",
  "required": ["candidate_id", "run_id", "target_symbol_name", "candidate_code", "validation_status"],
  "properties": {
    "candidate_id": { "type": "string", "format": "uuid" },
    "run_id": { "type": "string", "format": "uuid" },
    "target_symbol_name": { "type": "string" },
    "candidate_code": { "type": "string" },
    "imports": {
      "type": "array",
      "items": { "type": "string" }
    },
    "validation_status": {
      "type": "string",
      "enum": ["PENDING", "PASSED", "REJECTED_SYNTAX", "REJECTED_SECURITY", "REJECTED_COLLECTION", "QUARANTINED"]
    },
    "quarantine_reason": { "type": ["string", "null"] },
    "retry_count": { "type": "integer", "minimum": 0 }
  }
}
```

#### 4.4.7.3 JSON Schema Contract: ExecutionEvidence
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ExecutionEvidenceContract",
  "type": "object",
  "required": ["evidence_id", "run_id", "candidate_id", "exit_code", "duration_sec", "timed_out"],
  "properties": {
    "evidence_id": { "type": "string", "format": "uuid" },
    "run_id": { "type": "string", "format": "uuid" },
    "candidate_id": { "type": "string" },
    "exit_code": { "type": "integer" },
    "stdout": { "type": "string" },
    "stderr": { "type": "string" },
    "duration_sec": { "type": "number", "minimum": 0.0 },
    "timed_out": { "type": "boolean" },
    "line_coverage": { "type": ["number", "null"], "minimum": 0.0, "maximum": 100.0 },
    "branch_coverage": { "type": ["number", "null"], "minimum": 0.0, "maximum": 100.0 },
    "traceback": { "type": ["string", "null"] }
  }
}
```

---

### 4.4.8 Problem Domain (PD) Layer Packaging Design
Following the object-oriented architectural packaging conventions articulated by Dennis, Wixom, and Tegarden (Systems Analysis and Design with UML 2.0), the pure business logic of Increment 1 (Layer 3: Domain Services Layer) is organized into cohesive, modular packages encapsulated within the **Problem Domain (PD) Layer**. **Figure 4.8** presents the comprehensive Problem Domain Package Diagram, illustrating domain service distribution, internal class collaborations, inter-package dependency flows, and boundary inversion across protocol abstractions.

```text
[ Refer to Figure 4.8: Increment 1 Problem Domain (PD) Layer Package Diagram ]
```

#### 4.4.8.1 Problem Domain Package Taxonomy
The Problem Domain Layer groups the core services into six cohesive domain packages according to functional affinity and lifecycle boundaries:

1. **Analysis Pkg (`agentic_test.domain.analysis`)**:
   * *Encapsulated Classes*: `AnalysisService`, `TestDiscovery`.
   * *Responsibilities*: Parses the target repository AST, extracts symbol dependency graphs, detects existing pytest test suites, and maps modified source lines to candidate target symbols.
   * *Internal Collaboration*: `AnalysisService` delegates repository file-system inspection and existing test suite mapping to `TestDiscovery`.

2. **Planning Pkg (`agentic_test.domain.planning`)**:
   * *Encapsulated Classes*: `ExecutionPlanner`, `ContextAssembler`.
   * *Responsibilities*: Formulates deterministic, acyclic execution plans based on symbol dependency topological sorting and constructs token-bounded LLM prompt contexts with symbol signatures, docstrings, and diff hunks.
   * *Internal Collaboration*: `ExecutionPlanner` structures the candidate queue, while `ContextAssembler` ensures prompt payloads strictly respect model token ceilings (`NFR-04`).

3. **Generation Pkg (`agentic_test.domain.generation`)**:
   * *Encapsulated Classes*: `GenerationService`.
   * *Responsibilities*: Coordinates LLM prompt transmission, parses structured JSON test candidates, and executes localized candidate repair retry loops (`REQ-05`) upon syntax or structural rejection prior to formal validation admission.
   * *Internal Collaboration*: Consumes prompt templates prepared by `ContextAssembler` from the `Planning Pkg`.

4. **Validation Pkg (`agentic_test.domain.validation`)**:
   * *Encapsulated Classes*: `ValidationPipeline`, `BaseValidator` (abstract), `SyntaxValidator`, `SecurityValidator`, `CollectionValidator`.
   * *Responsibilities*: Implements the deterministic multi-gate admission barrier (`REQ-06`). Non-regenerative filter evaluating AST validity, prohibited built-ins, and pytest collection execution without re-invoking LLMs.
   * *Internal Collaboration*: `ValidationPipeline` aggregates and executes a polymorphic chain of `BaseValidator` implementations through composition (`1..*`).

5. **Execution Pkg (`agentic_test.domain.execution`)**:
   * *Encapsulated Classes*: `ExecutionService`, `PytestRunner`, `CoverageExtractor`.
   * *Responsibilities*: Orchestrates containerized test execution in isolated sandboxes, captures execution artifacts (stdout, stderr, exit codes, execution duration), and computes line/branch coverage deltas.
   * *Internal Collaboration*: `ExecutionService` uses `PytestRunner` to format CLI arguments and parse test results, and delegates JSON coverage parsing and delta calculation to `CoverageExtractor`.

6. **Diagnosis Pkg (`agentic_test.domain.diagnosis`)**:
   * *Encapsulated Classes*: `DiagnosisService`, `DeterministicRuleClassifier`, `CognitiveLLMClassifier`.
   * *Responsibilities*: Executes two-tier triage on failed test runs (`REQ-08`, `NFR-02`). Evaluates fast deterministic regex rules first, cascading to cognitive LLM classification only when rule evaluation yields ambiguous or novel failure modes.
   * *Internal Collaboration*: `DiagnosisService` delegates first to `DeterministicRuleClassifier` and conditionally invokes `CognitiveLLMClassifier` for complex failures.

#### 4.4.8.2 Dependency Inversion Across Protocol Boundaries
To satisfy the Dependency Inversion Principle (DIP) and isolate the problem domain from technology volatility, the three formal protocol abstractions (`CodeAnalyzer`, `SandboxManager`, `LLMService`) are positioned outside the `PD Layer` container:
* **CodeAnalyzer Protocol (`typing.Protocol`)**: Sits above `Analysis Pkg`. `AnalysisService` depends exclusively on this abstraction, enabling language parser substitution (Python AST vs. Tree-sitter) with zero modification to domain analysis logic.
* **SandboxManager Protocol (`typing.Protocol`)**: Sits below `Execution Pkg`. `ExecutionService` interacts solely through container lifecycle contracts (`create_environment`, `execute_command`, `cleanup`), decoupling test execution from specific virtualization engines (Docker, Podman, gVisor).
* **LLMService Protocol (`typing.Protocol`)**: Sits beside `Generation Pkg` and `Diagnosis Pkg`. Both `GenerationService` and `CognitiveLLMClassifier` invoke structured generation through this abstract interface, enabling provider switching (Anthropic, OpenAI, local Ollama) without modifying prompt orchestration or triage algorithms.

#### 4.4.8.3 Inter-Package Coupling & Pipeline Flow
The Problem Domain Layer follows a directed, acyclic serpentine execution flow:
$$\text{Analysis Pkg} \xrightarrow{\text{RepositorySnapshot}} \text{Planning Pkg} \xrightarrow{\text{Prompt Context}} \text{Generation Pkg} \xrightarrow{\text{TestCandidate}} \text{Validation Pkg} \xrightarrow{\text{Validated Candidate}} \text{Execution Pkg} \xrightarrow{\text{ExecutionEvidence}} \text{Diagnosis Pkg}$$

This unidirectional flow prevents cyclic dependencies, ensures high package cohesion, and provides discrete architectural test seams where any package can be unit-tested or substituted in isolation.

---

