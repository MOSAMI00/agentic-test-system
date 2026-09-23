# Chapter 4: Increment 1: Automated Test Generation — Detailed Design, Implementation, and Verification

---

## 4.1 Introduction

### 4.1.1 Engineering Context and Motivation
Software verification remains one of the most labor-intensive phases of modern software engineering. While modern Continuous Integration and Continuous Delivery (CI/CD) pipelines enforce automated test execution, the synthesis, curation, and maintenance of unit tests remain overwhelmingly manual. Recent advancements in Large Language Models (LLMs) have demonstrated substantial promise in synthesizing source code; however, applying generative models directly to software testing introduces critical failure modes documented across empirical literature:
1. **Syntactic and Semantic Hallucination**: Generative models frequently fabricate non-existent API symbols, invalid keyword arguments, or unimported fixtures.
2. **Untrusted Code Execution Vulnerabilities**: Directly executing LLM-generated test code on developer workstations or host CI runners exposes systems to arbitrary code execution, filesystem corruption, or outbound network exfiltration.
3. **Flakiness and Non-Determinism**: Generative tests often rely on uncontrolled environment state, unbounded execution times, or fluctuating model temperatures.
4. **Lack of Deterministic Pre-Routing**: Invoking expensive, probabilistic cognitive models indiscriminately—even when changes are trivial, non-functional, or already fully covered by existing suites—incurs unwarranted latency, financial cost, and token consumption.

To overcome these deficiencies, this research project develops an **Agentic Test Generation and Maintenance System**. Rather than treating test generation as an unconstrained chat completion task, the system structures test lifecycle management into an orchestrated, multi-stage engineering process governed by deterministic static analysis, strict architectural invariants, multi-gate pre-execution validation, ephemeral container sandboxing, and dual-engine failure triage.

### 4.1.2 Prioritization of Increment 1: Generation-to-Execution Baseline
The system development is structured into three evolutionary, self-contained increments:
* **Increment 1: Automated Unit Test Generation & Sandboxed Verification Baseline** (the focus of this chapter);
* **Increment 2: Automated Test Maintenance & Healing** (updating outdated or broken test suites following upstream refactoring);
* **Increment 3: Human-in-the-Loop (HITL) Governance & Interactive Decision Orchestration** (developer mediation, patch auditing, and pull request synthesis).

Increment 1 is intentionally prioritized as the foundational baseline of the entire architecture. An automated system cannot meaningfully heal broken tests (Increment 2) or present trustworthy patch proposals to software engineers (Increment 3) without first establishing:
1. A deterministic static analysis engine capable of mapping source code ASTs, Git working tree diffs, and symbol dependencies without executing untrusted code.
2. A deterministic planning router that prevents unneeded cognitive model invocation.
3. A multi-gate validation pipeline that filters non-syntactic or malicious test candidates before execution.
4. A secure, hermetic container sandbox guaranteeing total network isolation, memory and CPU constraints, and physical read-only protection of production source trees.
5. An execution evidence collection engine that accurately extracts pytest status, stdout/stderr streams, execution durations, and line/branch coverage metrics.
6. A dual-engine failure triage subsystem capable of classifying execution anomalies across a canonical 7-category taxonomy.

By proving the stability, correctness, and safety of this generation-to-execution verification loop in Increment 1, subsequent increments can consume immutable execution evidence and diagnostic records as verified primitives, without re-implementing infrastructure or compromising host security.

### 4.1.3 Chapter Organization Across the 7-Stage Engineering Lifecycle
This chapter presents the comprehensive engineering realization of Increment 1 across seven sequential development stages:
* **Stage 1: Increment 1 Planning & Scoping (Section 4.2)**: Establishes the 6-iteration Work Breakdown Structure (WBS), formal scope boundaries, milestone schedule, environment prerequisites, and technical risk mitigation matrix.
* **Stage 2: Analysis & Specification Refinement (Section 4.3)**: Formulates refined Functional Requirements (FR-01 through FR-20 across six domains), operational use-case walkthroughs (UC-01 through UC-06), the domain entity model, and mathematical execution safety invariants.
* **Stage 3: Detailed Architectural & Component Design (Section 4.4)**: Defines the 5-layer subsystem architecture, formal Python protocol contracts (`CodeAnalyzer`, `SandboxManager`, `LLMService`), LangGraph state machine, component specifications, UML Class and Sequence diagrams, and SQLite relational persistence schemas.
* **Stage 4: Implementation & Engineering Realization (Section 4.5)**: Documents the physical repository layout (`agentic_test` package), core protocol implementations, domain business services, LangGraph node routing, telemetry event store, and interactive Typer CLI.
* **Stage 5: Verification & Empirical Testing (Section 4.6)**: Demonstrates system quality through multi-tier testing, unit test coverage, infrastructure adapter tests, 5-vector sandbox security penetration tests, benchmark repository evaluations, failure classification accuracy benchmarks, and the 33-requirement traceability matrix.
* **Stage 6: Review, Refinement & Engineering Evaluation (Section 4.7)**: Conducts an architectural conformance audit, latency and bottleneck profiling, and cataloging of technical debt.
* **Stage 7: Release 1 Deliverables & Operational Demonstration (Section 4.8)**: Details the physical release package, CLI operational user guide, and an end-to-end execution trace on a concrete Python codebase.
* **Chapter Summary & Forward Transition (Section 4.9)**: Summarizes core engineering contributions and articulates the clean architectural handoff to Increment 2.

---

## 4.2 Stage 1: Increment 1 Planning & Scoping

### 4.2.1 Iteration Decomposition & Work Breakdown Structure (WBS)
To manage architectural complexity and guarantee incremental verification, Increment 1 is decomposed into six focused, sequential iterations (Iterations 1.1 through 1.6). Each iteration builds strictly upon the verified primitives of its predecessors, adhering to a bottom-up software construction paradigm. Table 4.2 details the Work Breakdown Structure, delineating target functional and non-functional requirements, engineering tasks, deliverable artifacts, and acceptance criteria.

#### Table 4.2: Increment 1 Work Breakdown Structure: Iterations 1.1 through 1.6

| Iteration ID & Title | Target Requirements | Engineering Tasks | Deliverable Artifacts | Acceptance Criteria |
| :--- | :--- | :--- | :--- | :--- |
| **Iteration 1.1: Core Infrastructure, State Contracts & Repository Ingestion** | FR-01, FR-02, NFR-01, NFR-06 | 1. Initialize Python package layout (`agentic_test`).<br>2. Define immutable Pydantic `WorkflowState` and sub-contexts.<br>3. Implement `GitService` to validate local Git repo, extract HEAD/base commits, and compute working tree diffs.<br>4. Exclude `.git`, virtual environments, and caches. | `agentic_test.core.models`, `agentic_test.core.state`, `agentic_test.analysis.git_service`, `test_git_service.py` | 1. Successfully validates valid Git repo paths and rejects non-repo directories.<br>2. Correctly parses unified diff hunks without executing repository code.<br>3. Pydantic models validate and serialize immutably. |
| **Iteration 1.2: AST Parsing, Symbol Extraction & Static Dependency Analysis** | FR-03, FR-04, NFR-11 | 1. Define `CodeAnalyzer` formal protocol interface.<br>2. Implement `PythonASTAnalyzer` using standard library `ast`.<br>3. Extract modules, classes, functions, type annotations, signatures, and docstrings.<br>4. Implement `TestDiscovery` to map existing tests to symbols.<br>5. Intersect diff hunks with symbols to identify `affected_symbols`. | `agentic_test.core.protocols.analyzer`, `agentic_test.analysis.ast_analyzer`, `agentic_test.analysis.discovery`, `test_ast_analyzer.py` | 1. Zero execution of target code during static analysis.<br>2. 100% extraction accuracy on valid Python 3.11 ASTs.<br>3. Correctly identifies changed functions/methods based on diff line ranges. |
| **Iteration 1.3: Deterministic Planning & Decision Routing Gate** | FR-05, FR-06, NFR-02 | 1. Implement `ExecutionPlanner` using pure Python deterministic rules.<br>2. Evaluate `git_diff`, `affected_symbols`, and `existing_tests`.<br>3. Implement routing decisions: `ROUTE_NO_OP`, `ROUTE_TO_DOCKER_EXECUTION`, and `ROUTE_TO_TEST_GENERATION`.<br>4. Record structured `ExecutionPlan` with rationale and decision hashes. | `agentic_test.planning.planner`, `agentic_test.planning.rules`, `test_execution_planner.py` | 1. Zero LLM calls when working tree has no changes (`ROUTE_NO_OP`).<br>2. Zero LLM calls when affected symbols already possess passing tests (`ROUTE_TO_DOCKER_EXECUTION`).<br>3. Mathematical certainty of route selection. |
| **Iteration 1.4: Cognitive Test Generation & Multi-Gate Validation Pipeline** | FR-07, FR-08, FR-09, FR-10, NFR-04, NFR-13 | 1. Define `LLMService` protocol and implement `LiteLLMService` adapter.<br>2. Implement `ContextAssembler` to assemble compact, symbol-focused prompts.<br>3. Implement `GenerationService` with structured JSON output parsing and bounded internal candidate repair.<br>4. Implement 3-gate `ValidationPipeline` as a final acceptance boundary: Gate 1 (`ast.parse`), Gate 2 (Bandit static security AST visitor), Gate 3 (`pytest --collect-only`).<br>5. Implement quarantine mechanism for invalid candidates. | `agentic_test.core.protocols.llm`, `agentic_test.generation.generator`, `agentic_test.generation.context`, `agentic_test.validation.pipeline`, `agentic_test.validation.gates`, `test_validation_pipeline.py` | 1. Synthesizes valid `pytest` test functions matching target symbol signatures, performing bounded internal repair for malformed outputs prior to validation.<br>2. Rejects 100% of non-syntactic candidates in Gate 1 to quarantine without initiating regeneration.<br>3. Blocks unsafe imports (`os.system`, `subprocess`, sockets) in Gate 2 with permanent quarantine.<br>4. Verified collection in Gate 3. |
| **Iteration 1.5: Isolated Docker Sandbox Execution & Coverage Measurement** | FR-11, FR-12, FR-13, FR-14, FR-15, FR-16, FR-17, NFR-03, NFR-08, NFR-12 | 1. Define `SandboxManager` protocol and implement `DockerSandboxManager`.<br>2. Build hardened runner Docker image (`agentic-runner:0.1.0`).<br>3. Enforce `--network none`, `:ro` source mounts, 512MB RAM, 1.0 CPU, 30s timeout.<br>4. Implement `ExecutionService` and `PytestRunner`.<br>5. Implement `CoverageExtractor` to parse `.coverage` and `coverage.json`. | `agentic_test.core.protocols.sandbox`, `agentic_test.execution.docker_sandbox`, `agentic_test.execution.runner`, `agentic_test.execution.coverage`, `docker/Dockerfile.runner`, `test_docker_sandbox.py` | 1. Network completely disabled inside container (socket connections fail).<br>2. Application source directory verified unmodified via pre/post SHA-256 hashes.<br>3. Container killed cleanly on timeout without host hang.<br>4. Accurate extraction of exit code, stdout/stderr, and coverage deltas. |
| **Iteration 1.6: Dual-Engine Failure Triage, State Persistence & CLI Integration** | FR-18, FR-19, FR-20, NFR-05, NFR-07 | 1. Implement `DiagnosisService` with dual-engine architecture: Rule-based triage (Regex/error classification) and Cognitive LLM triage for ambiguous failures.<br>2. Classify failures across the canonical 7 categories.<br>3. Flag `APPLICATION_BUG` for developer review without auto-repair.<br>4. Implement SQLite database checkpointer and append-only event store.<br>5. Implement interactive `Typer` CLI with `Rich` formatting. | `agentic_test.diagnosis.service`, `agentic_test.diagnosis.rules`, `agentic_test.diagnosis.llm_classifier`, `agentic_test.storage.database`, `agentic_test.storage.events`, `agentic_test.cli.app`, `test_diagnosis_service.py` | 1. Deterministic rules immediately resolve `CONFIGURATION_ERROR` and `ENVIRONMENT_FAILURE` (100% accuracy, zero LLM tokens).<br>2. Cognitive triage correctly differentiates `TEST_OUTDATED` from `APPLICATION_BUG`.<br>3. Complete execution state persisted in SQLite.<br>4. CLI runs end-to-end with structured terminal reporting. |

---

### 4.2.2 Increment 1 Scope Boundaries & Milestone Schedule
To maintain strict architectural discipline, the boundaries of Increment 1 are rigorously demarcated. Increment 1 focuses exclusively on generating and verifying unit tests for uncovered or newly modified Python symbols. Table 4.3 outlines the formal milestone schedule, target delivery dates, verification evidence artifacts, and the explicit architectural inclusions and exclusions governing Increment 1.

#### Table 4.3: Increment 1 Delivery Milestones & Boundary Matrix

| Milestone ID | Target Schedule | Milestone Scope Description | Required Verification Artifact | Gate Criteria |
| :--- | :--- | :--- | :--- | :--- |
| **M1.1: Core Infrastructure & Ingestion** | Day 1–4 | Package skeleton, immutable Pydantic models, Git working tree ingestion, unified diff parser. | Unit test suite: `test_git_service.py`, `test_state_models.py` | 100% model serialization pass rate; zero external process leaks; Git repository paths validated. |
| **M1.2: Static Analysis & Symbol Mapping** | Day 5–8 | Standard AST symbol extractor, dependency mapper, test discovery engine, symbol diff cross-referencer. | Unit test suite: `test_ast_analyzer.py`; sample AST JSON dump | Zero target code execution during parsing; all functions/methods/classes mapped with line numbers. |
| **M1.3: Deterministic Planner** | Day 9–11 | Pure Python decision router; precedence rules; elimination of unnecessary LLM calls. | Routing test suite: `test_execution_planner.py`; Decision logs | Deterministic routing table passes all boundary conditions with zero model calls. |
| **M1.4: Generation & Multi-Gate Validation** | Day 12–17 | LiteLLM abstraction, prompt context assembly, candidate synthesis with bounded candidate repair, and final non-regenerative 3-gate validation pipeline. | Validation suite: `test_validation_pipeline.py`; synthetic invalid test corpus | Internal candidate repair resolves malformed outputs before validation; final validation pipeline rejects 100% of invalid syntax and security-flagged imports directly to quarantine with zero regeneration loops. |
| **M1.5: Docker Sandbox & Coverage** | Day 18–24 | Docker SDK sandbox, hardened runner container, resource limits, pytest execution, coverage parser. | Security attack suite: `test_sandbox_security.py`; Docker test trace | Enforced network isolation; read-only source code integrity confirmed; coverage delta captured. |
| **M1.6: Dual-Engine Triage, Persistence & CLI** | Day 25–30 | Deterministic rule triage, cognitive LLM classifier, SQLite checkpointer, Typer/Rich CLI release. | Full end-to-end benchmark run; SQLite database file; CLI terminal log | 7-category taxonomy classified; end-to-end execution passes on benchmark repository. |

#### Architectural Scope Inclusions vs. Exclusions for Increment 1
* **Strict Inclusions**:
  * Python projects (Python 3.11+);
  * Unit testing framework: `pytest` and `pytest-cov`;
  * Local Git repositories;
  * Docker-based containerized test execution;
  * Multi-gate static candidate validation (Syntax, Bandit security AST visitor, Collection) operating as a final non-regenerative acceptance gate;
  * Isolated dynamic execution with `--network none`, `:ro` volume bindings, and hard resource caps;
  * Dual-engine failure triage into the canonical 7 categories;
  * SQLite event sourcing and state checkpointing;
  * Typer-based command-line interface with Rich formatting.
* **Strict Exclusions**:
  * **Zero Production Source Code Modification**: The system is physically and programmatically barred from mutating any file in `src/` or the production application tree.
  * **Zero Automated Bug Fixing**: The system never attempts to repair an identified application defect; apparent defects are flagged as `APPLICATION_BUG` and isolated for human developer review.
  * **Zero Automated Repository Commits/Pushes**: The system does not execute `git commit`, `git merge`, or `git push`. Generated candidates reside strictly in isolated artifact workspaces.
  * **No Test Maintenance Patching**: Automated updating or healing of broken existing test files is explicitly deferred to **Increment 2**.
  * **No Interactive HITL Approval Loop**: Interactive developer approval workflows, browser-based reviews, and patch application are explicitly deferred to **Increment 3**.
  * **No Multi-Language Support**: Target code is strictly Python; languages such as Java, TypeScript, or Go are out of scope for the MVP.
  * **No Non-Unit Test Generation**: Integration, End-to-End, GUI, and performance testing are excluded.

---

### 4.2.3 Development, Containerization, and Execution Environment Prerequisites
To guarantee absolute reproducibility and satisfy NFR-08 (Reproducibility) and NFR-12 (Sandbox Portability), the system specifies strict host environment baselines, container execution parameters, and software dependency versions.

#### 1. Host Operating System and Hardware Baseline
* **Operating System**: Linux (Ubuntu 22.04 LTS or newer), Windows 11 with WSL2 (Ubuntu 22.04), or macOS 14.0+ (Apple Silicon / x86_64).
* **Processor Architecture**: 64-bit x86_64 or ARM64, minimum 4 physical CPU cores (8 cores recommended for concurrent sandbox execution).
* **System Memory**: Minimum 8.0 GB RAM (16.0 GB recommended to allocate 512 MB memory quotas per container instance comfortably).
* **Storage Requirements**: Minimum 10.0 GB of available SSD storage for host virtual environments, Docker images, SQLite database files, and run telemetry artifacts.

#### 2. Host Python Runtime and Virtual Environment
* **Python Runtime**: Python 3.11.8 or higher (tested on CPython 3.11.8 and 3.12.3).
* **Packaging and Dependency Management**: `pip` (>= 24.0) and `virtualenv` / `venv`.
* **Core Python Dependencies**:
  * `langgraph` (>= 0.2.14): Orchestration state graph and checkpointer mechanics.
  * `litellm` (>= 1.44.0): Unified gateway abstraction for multi-provider LLM access.
  * `docker` (>= 7.1.0): Official Docker SDK for Python managing container lifecycles.
  * `pydantic` (>= 2.8.2) & `pydantic-settings` (>= 2.4.0): Strict schema definition and validated configuration.
  * `gitpython` (>= 3.1.43): Deterministic Git working tree inspection and diff generation.
  * `typer[all]` (>= 0.12.3): Modern CLI application development with Rich formatting.
  * `rich` (>= 13.7.1): Structured terminal tables, syntax highlighting, and progress indicators.
  * `structlog` (>= 24.4.0): Structured, context-rich JSON/key-value application logging.
  * `bandit` (>= 1.7.9): Static security AST analysis for candidate validation Gate 2.
  * `pytest` (>= 8.3.2) & `pytest-cov` (>= 5.0.0): Test discovery, execution, and coverage analysis.

#### 3. Container Daemon and Runner Image Specification
* **Docker Daemon**: Docker Engine version 24.0.0 or higher / Docker Desktop 4.25.0+. The daemon must support Linux containers, cgroups v2 resource capping (`--memory`, `--cpus`, `--pids-limit`), and custom bridge/null network drivers.
* **Hardened Runner Image (`agentic-runner:0.1.0`)**:
  * Base OS: `python:3.11-slim-bookworm`.
  * Security Context: Configured with a dedicated non-privileged user `sandboxuser` (`uid=10001`, `gid=10001`). Root execution is explicitly prohibited.
  * Pre-installed Packages: `pytest`, `pytest-cov`, `pytest-mock`, `pytest-timeout`, and standard testing utilities.
  * Working Directory: `/workspace` (isolated ephemeral volume mount).

#### 4. Cognitive Model Integration and API Credentials
* **Primary LLM Provider**: OpenAI API utilizing `gpt-4o-mini` (or `gpt-4o`) accessed through the `litellm` SDK gateway.
* **Temperature Setting**: Explicitly pinned to `temperature = 0.0` (or `0.2`) to maximize determinism and syntax stability.
* **Environment Credentials**: System expects `OPENAI_API_KEY` defined in the host environment or a secure `.env` file. All secrets are encapsulated within `pydantic.SecretStr` to prevent inadvertent leakage into logs, telemetry traces, or terminal outputs.

---

### 4.2.4 Engineering Risk Assessment & Proactive Mitigation Matrix
The automated generation and execution of software tests introduce severe technical risks, ranging from LLM syntax hallucinations to catastrophic container escape attempts or host filesystem corruption. To safeguard the host system and ensure reliable execution, Table 4.4 presents a rigorous risk assessment identifying seven critical engineering risks, their likelihood and impact, and the proactive architectural mitigations engineered into Increment 1.

#### Table 4.4: Increment 1 Risk Assessment and Engineering Mitigation Matrix

| Risk ID | Identified Technical Risk | Likelihood | Impact | Proactive Architectural Mitigation |
| :--- | :--- | :--- | :--- | :--- |
| **RSK-01** | **LLM Syntax & Formatting Hallucination**<br>The LLM generates invalid Python syntax, unclosed brackets, markdown commentary within code blocks, or references non-existent symbols. | High | High | **Bounded Internal Candidate Repair & Non-Regenerative Gate 1**:<br>1. *Internal Candidate Repair*: `GenerationService` enforces bounded regeneration with feedback (up to 2 retries) for repairable generation defects (malformed JSON, incomplete syntax) prior to validation submission.<br>2. *Final Validation Gate*: Submitted candidates are parsed with `ast.parse`. Syntax errors trigger immediate rejection to quarantine with zero downstream regeneration loops, preventing wasted container spin-up. |
| **RSK-02** | **Malicious Code Execution & Container Escape**<br>Generated test code contains malicious payloads (e.g., `os.system('rm -rf /')`, socket connections, privilege escalation, or host filesystem tampering). | Medium | Critical | **Dual-Layer Defense (Gate 2 Static Security + Hardened Docker Sandbox)**:<br>1. *Gate 2 Security Scanner*: Bandit-based AST visitor blocks calls to `eval`, `exec`, `subprocess`, `socket`, `os.system`, and unsafe file writes.<br>2. *Sandbox Confinement*: Docker container executes with `--network none`, `--cap-drop all`, `--security-opt no-new-privileges`, as non-root `uid=10001`. |
| **RSK-03** | **Production Source-Code Mutation / Corruption**<br>Test execution or maintenance logic accidentally modifies or overwrites application source code files under `src/`. | Medium | Critical | **Physical Read-Only Volume Mounting (`:ro`) & Cryptographic Verification**:<br>1. The target application repository is mounted into the container strictly with read-only flags (`:ro`). Any attempted write triggers an immediate OS-level `EROFS` (Read-only file system) exception.<br>2. Pre-execution and post-execution SHA-256 hashes of all repository source files are computed and compared. Any mismatch halts the pipeline and triggers a critical integrity violation alert. |
| **RSK-04** | **Infinite Loops & Resource Starvation**<br>Generated test cases execute non-terminating `while` loops, recursive stack overflows, or allocate excessive memory, freezing the orchestrator. | High | High | **Strict Operating System Cgroups & Container Execution Timeouts**:<br>1. Hard wall-clock timeout of 30.0 seconds enforced by Docker daemon; container is forcibly terminated with `SIGKILL` if exceeded.<br>2. Kernel cgroup constraints enforced: `--memory 512m`, `--cpus 1.0`, `--pids-limit 100` (blocking fork-bombs and memory ballooning). |
| **RSK-05** | **Test Flakiness & Non-Deterministic Assertions**<br>Generated tests produce oscillating pass/fail outcomes due to reliance on system clocks, unseeded random number generators, or network dependencies. | Medium | Medium | **Hermetic Sandbox Environment & Deterministic LLM Parameters**:<br>1. Total network disallowance prevents reliance on remote endpoints.<br>2. LLM sampling temperature pinned to `0.0` with static prompt scaffolding instructing tests to use deterministic fixtures and seed random generators.<br>3. Isolated system environment variables and UTC clock configuration within container. |
| **RSK-06** | **LLM API Rate Limiting & Upstream Service Outages**<br>API quota exhaustion, network throttling, or temporary downtime from the LLM provider interrupts workflow execution. | Medium | Medium | **LiteLLM Gateway Resilience & Exponential Backoff Retries**:<br>1. `LiteLLMService` encapsulates API invocations with exponential backoff and jitter across transient HTTP 429/500/503 errors.<br>2. Configurable fallback model chain (e.g., fallback from `gpt-4o-mini` to local Ollama or Claude endpoint).<br>3. AST analysis and symbol extraction are 100% offline and deterministic, preserving tokens. |
| **RSK-07** | **Orchestrator State Loss on Sudden Termination**<br>Host machine reboot, power loss, or unexpected process crash destroys execution history, coverage metrics, and diagnostic evidence. | Low | High | **SQLite State Sourcing & Transactional Checkpointing**:<br>LangGraph state machine is backed by an ACID-compliant SQLite checkpointer (`SqliteSaver`). Every node transition, candidate state, execution evidence block, and diagnosis record is transactionally committed to disk, enabling resume and complete auditability. |

---
