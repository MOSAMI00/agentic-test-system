"""
GenerationService orchestrating cognitive unit test synthesis for execution plans.
Iteration 1.4 Component Architecture.
"""

import ast
import uuid
from pathlib import Path
from typing import List, Optional

from agentic_test.core.models import (
    ExecutionPlan,
    SymbolContract,
    TestCandidate,
    ValidationStatus,
)
from agentic_test.core.protocols.llm import LLMService
from agentic_test.generation.context import (
    ContextAssembler,
    GenerationContext,
    SYSTEM_INSTRUCTION,
)
from agentic_test.generation.exceptions import (
    ContextBudgetExceededError,
    GenerationError,
    LLMCommunicationError,
    SchemaValidationError,
)
from agentic_test.generation.schemas import CandidateSynthesisSchema


R_MAX_REPAIRS: int = 2


class GenerationService:
    """
    Coordinates cognitive test synthesis for target symbols identified in an ExecutionPlan.
    Applies bounded pre-validation repairs (R_max = 2) before packaging candidates.
    """

    def __init__(
        self,
        llm_service: LLMService,
        context_assembler: Optional[ContextAssembler] = None,
        run_id: Optional[str] = None,
        repo_root: Optional[Path] = None,
    ) -> None:
        self._llm_service = llm_service
        self._context_assembler = context_assembler or ContextAssembler(llm_service=llm_service)
        self._run_id = run_id
        self._repo_root = repo_root

    def generate(self, plan: ExecutionPlan) -> List[TestCandidate]:
        """
        Generates TestCandidate instances strictly for plan.target_symbols.
        Pre-validation repairs are capped at R_max = 2.
        Unrepairable candidates are marked QUARANTINED with reason EXHAUSTED_GENERATION_REPAIRS.
        """
        candidates: List[TestCandidate] = []
        effective_run_id = self._run_id or plan.plan_id

        for target_symbol in plan.target_symbols:
            candidate = self._generate_candidate_for_symbol(
                target_symbol=target_symbol,
                run_id=effective_run_id,
            )
            candidates.append(candidate)

        return candidates

    def _generate_candidate_for_symbol(
        self,
        target_symbol: SymbolContract,
        run_id: str,
    ) -> TestCandidate:
        """
        Synthesizes and repairs a test candidate for an individual symbol.
        """
        candidate_id = f"tc-{uuid.uuid4().hex[:8]}"
        test_file_path = self._derive_test_file_path(target_symbol)

        # 1. Assemble context
        try:
            context = self._context_assembler.assemble(
                target_symbol=target_symbol,
                repo_root=self._repo_root,
            )
        except ContextBudgetExceededError as err:
            return TestCandidate(
                candidate_id=candidate_id,
                run_id=run_id,
                target_symbol_name=target_symbol.qualified_name,
                test_file_path=test_file_path,
                candidate_code="",
                imports=(),
                validation_status=ValidationStatus.QUARANTINED,
                quarantine_reason=f"CONTEXT_BUDGET_EXCEEDED: {err}",
                retry_count=0,
            )

        initial_prompt = self._context_assembler.format_prompt(context)
        current_prompt = initial_prompt
        repair_count = 0
        last_error_message = ""
        last_code = ""
        last_imports: tuple[str, ...] = ()

        # Pre-validation synthesis + repair loop (up to R_MAX_REPAIRS)
        while True:
            try:
                synthesis = self._llm_service.generate_structured(
                    prompt=current_prompt,
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_schema=CandidateSynthesisSchema,
                )
                last_code = synthesis.test_code
                last_imports = tuple(synthesis.imports)

                # Internal quick syntax check
                self._verify_syntax(last_code, last_imports)

                # Successfully synthesized and syntactically valid
                return TestCandidate(
                    candidate_id=candidate_id,
                    run_id=run_id,
                    target_symbol_name=target_symbol.qualified_name,
                    test_file_path=test_file_path,
                    candidate_code=last_code,
                    imports=last_imports,
                    validation_status=ValidationStatus.PENDING,
                    quarantine_reason=None,
                    retry_count=repair_count,
                )

            except (SchemaValidationError, SyntaxError, LLMCommunicationError) as err:
                repair_count += 1
                last_error_message = f"{type(err).__name__}: {err}"

                if repair_count > R_MAX_REPAIRS:
                    # Repair budget exhausted: quarantine candidate
                    return TestCandidate(
                        candidate_id=candidate_id,
                        run_id=run_id,
                        target_symbol_name=target_symbol.qualified_name,
                        test_file_path=test_file_path,
                        candidate_code=last_code,
                        imports=last_imports,
                        validation_status=ValidationStatus.QUARANTINED,
                        quarantine_reason="EXHAUSTED_GENERATION_REPAIRS",
                        retry_count=repair_count - 1,
                    )

                # Prepare repair prompt
                current_prompt = self._build_repair_prompt(
                    initial_prompt=initial_prompt,
                    previous_code=last_code,
                    error_message=last_error_message,
                    attempt_number=repair_count,
                )

    def _verify_syntax(self, code: str, imports: tuple[str, ...]) -> None:
        """Verifies candidate code forms valid Python syntax."""
        combined_source = "\n".join(imports) + "\n\n" + code
        ast.parse(combined_source)

    def _build_repair_prompt(
        self,
        initial_prompt: str,
        previous_code: str,
        error_message: str,
        attempt_number: int,
    ) -> str:
        """Constructs bounded repair prompt detailing previous failure."""
        return (
            f"{initial_prompt}\n\n"
            f"=== PREVIOUS SYNTHESIS ATTEMPT (Attempt {attempt_number}) ===\n"
            f"{previous_code}\n\n"
            f"=== REPAIR INSTRUCTION ===\n"
            f"The previous attempt failed with the following error:\n{error_message}\n"
            f"Please correct the error, fix syntax or schema issues, and return a clean, "
            f"compliant unit test candidate."
        )

    def _derive_test_file_path(self, target_symbol: SymbolContract) -> Path:
        """Derives standard pytest file path for target symbol."""
        stem = target_symbol.file_path.stem
        if not stem.startswith("test_"):
            stem = f"test_{stem}"
        return Path("tests") / f"{stem}.py"
