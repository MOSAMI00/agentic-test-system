"""
Context assembly and prompt preparation for cognitive test generation.
Iteration 1.4 Component Architecture.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from pydantic import BaseModel, ConfigDict, Field

from agentic_test.core.models import DiffHunk, SymbolContract
from agentic_test.core.protocols.llm import LLMService
from agentic_test.generation.exceptions import ContextBudgetExceededError


DEFAULT_MAX_TOKEN_BUDGET: int = 4000

SYSTEM_INSTRUCTION: str = (
    "You are an expert Python test engineer generating robust, isolated pytest unit tests. "
    "Adhere strictly to standard pytest conventions. Use unittest.mock or in-memory fixtures. "
    "Never generate direct filesystem write operations, shell commands, or network calls. "
    "Output must conform strictly to the required structured schema."
)

# Secret pattern regexes for sanitization
SECRET_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),  # OpenAI/API key pattern
    re.compile(r"ghp_[a-zA-Z0-9]{36,}"),  # GitHub personal access token
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}"),  # Bearer token
    re.compile(r"(?i)(api[_-]?key|secret|password|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
]


def mask_secrets(text: str) -> str:
    """
    Sanitizes string by replacing detected secret patterns with [REDACTED_SECRET].
    """
    sanitized = text
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    return sanitized


class GenerationContext(BaseModel):
    """
    Structured context package prepared for cognitive model test synthesis.
    """
    model_config = ConfigDict(frozen=True)

    target_symbol: SymbolContract
    diff_content: str
    source_code: str
    dependencies: Tuple[str, ...] = Field(default_factory=tuple)
    existing_test_samples: Tuple[str, ...] = Field(default_factory=tuple)
    token_count: int = 0


class ContextAssembler:
    """
    Assembles, sanitizes, and prunes context for cognitive test generation.
    Enforces a strict token ceiling with 3-tier progressive pruning.
    """

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        max_token_budget: int = DEFAULT_MAX_TOKEN_BUDGET,
    ) -> None:
        self._llm_service = llm_service
        self._max_token_budget = max_token_budget

    @property
    def max_token_budget(self) -> int:
        return self._max_token_budget

    def estimate_tokens(self, text: str) -> int:
        """Estimates token count via LLMService or conservative heuristic."""
        if self._llm_service is not None:
            return self._llm_service.estimate_tokens(text)
        # Fallback offline heuristic: 1 token ~= 4 characters
        return max(1, len(text) // 4)

    def assemble(
        self,
        target_symbol: SymbolContract,
        repo_root: Optional[Path] = None,
        diff_hunks: Sequence[DiffHunk] = (),
        existing_test_samples: Sequence[str] = (),
        source_code_override: Optional[str] = None,
    ) -> GenerationContext:
        """
        Assembles, sanitizes, and validates context for the given target symbol.
        Applies 3-tier pruning if token budget is exceeded.
        """
        # 1. Resolve source code
        if source_code_override is not None:
            raw_source = source_code_override
        else:
            raw_source = self._extract_symbol_source(target_symbol, repo_root)

        # 2. Extract and format diff content relevant to target symbol
        diff_text = self._format_diff_content(target_symbol, diff_hunks)

        # 3. Apply secret masking to all ingested text
        source_code = mask_secrets(raw_source)
        diff_content = mask_secrets(diff_text)
        dependencies = tuple(mask_secrets(d) for d in target_symbol.dependencies)
        test_samples = tuple(mask_secrets(s) for s in existing_test_samples)

        # 4. Multi-tier progressive pruning against token budget
        return self._prune_and_package(
            target_symbol=target_symbol,
            diff_content=diff_content,
            source_code=source_code,
            dependencies=dependencies,
            test_samples=test_samples,
        )

    def _extract_symbol_source(
        self, target_symbol: SymbolContract, repo_root: Optional[Path]
    ) -> str:
        """Reads target symbol source lines from disk if available, else signature."""
        full_path: Path
        if repo_root is not None and not target_symbol.file_path.is_absolute():
            full_path = repo_root / target_symbol.file_path
        else:
            full_path = target_symbol.file_path

        if full_path.is_file():
            try:
                lines = full_path.read_text(encoding="utf-8").splitlines()
                start, end = target_symbol.line_range
                # line_range is 1-indexed [start, end]
                symbol_lines = lines[max(0, start - 1) : end]
                return "\n".join(symbol_lines)
            except Exception:
                pass

        # Fallback to signature + docstring
        doc = f'    """{target_symbol.docstring}"""\n' if target_symbol.docstring else ""
        return f"{target_symbol.signature}:\n{doc}    ..."

    def _format_diff_content(
        self, target_symbol: SymbolContract, diff_hunks: Sequence[DiffHunk]
    ) -> str:
        """Filters diff hunks matching target file path and joins content."""
        relevant_hunks: List[str] = []
        for hunk in diff_hunks:
            if hunk.file_path == target_symbol.file_path or hunk.file_path.name == target_symbol.file_path.name:
                relevant_hunks.append(hunk.content)
        return "\n".join(relevant_hunks) if relevant_hunks else "No diff hunks recorded."

    def _prune_and_package(
        self,
        target_symbol: SymbolContract,
        diff_content: str,
        source_code: str,
        dependencies: Tuple[str, ...],
        test_samples: Tuple[str, ...],
    ) -> GenerationContext:
        """
        Applies 3-tier pruning:
        - Tier 1: Prune test samples and dependencies.
        - Tier 2: Prune docstrings from source code.
        - Tier 3: Core invariant (target symbol source + diff content).
        Raises ContextBudgetExceededError if minimal core exceeds budget.
        """
        # Baseline full candidate
        candidate_prompt = self._build_prompt_text(
            target_symbol, diff_content, source_code, dependencies, test_samples
        )
        total_tokens = self.estimate_tokens(candidate_prompt)

        if total_tokens <= self._max_token_budget:
            return GenerationContext(
                target_symbol=target_symbol,
                diff_content=diff_content,
                source_code=source_code,
                dependencies=dependencies,
                existing_test_samples=test_samples,
                token_count=total_tokens,
            )

        # Tier 1 Pruning: Drop test samples and dependencies
        dependencies = ()
        test_samples = ()
        candidate_prompt = self._build_prompt_text(
            target_symbol, diff_content, source_code, dependencies, test_samples
        )
        total_tokens = self.estimate_tokens(candidate_prompt)

        if total_tokens <= self._max_token_budget:
            return GenerationContext(
                target_symbol=target_symbol,
                diff_content=diff_content,
                source_code=source_code,
                dependencies=dependencies,
                existing_test_samples=test_samples,
                token_count=total_tokens,
            )

        # Tier 2 Pruning: Prune docstrings from source code
        source_code_no_docstrings = self._strip_docstrings(source_code)
        candidate_prompt = self._build_prompt_text(
            target_symbol, diff_content, source_code_no_docstrings, dependencies, test_samples
        )
        total_tokens = self.estimate_tokens(candidate_prompt)

        if total_tokens <= self._max_token_budget:
            return GenerationContext(
                target_symbol=target_symbol,
                diff_content=diff_content,
                source_code=source_code_no_docstrings,
                dependencies=dependencies,
                existing_test_samples=test_samples,
                token_count=total_tokens,
            )

        # Tier 3: Invariant Core Check
        # Even with minimal source and diff, the budget is exceeded
        raise ContextBudgetExceededError(
            f"Context budget exceeded: minimal core requires {total_tokens} tokens, "
            f"exceeding ceiling of {self._max_token_budget}."
        )

    def _strip_docstrings(self, code: str) -> str:
        """Removes triple-quoted docstrings from source code."""
        # Simple regex stripping for triple single and double quotes
        stripped = re.sub(r'"""[\s\S]*?"""', '"""[Docstring pruned]"""', code)
        stripped = re.sub(r"'''[\s\S]*?'''", "'''[Docstring pruned]'''", stripped)
        return stripped

    def _build_prompt_text(
        self,
        target_symbol: SymbolContract,
        diff_content: str,
        source_code: str,
        dependencies: Tuple[str, ...],
        test_samples: Tuple[str, ...],
    ) -> str:
        """Constructs full prompt representation for token estimation."""
        parts = [
            f"Target Symbol: {target_symbol.qualified_name} ({target_symbol.symbol_type.value})",
            f"Signature: {target_symbol.signature}",
            f"Source File: {target_symbol.file_path}",
            f"Source Code:\n{source_code}",
            f"Unified Diff:\n{diff_content}",
        ]
        if dependencies:
            parts.append(f"Dependencies:\n" + "\n".join(f"- {d}" for d in dependencies))
        if test_samples:
            parts.append(f"Existing Test Samples:\n" + "\n---\n".join(test_samples))
        return "\n\n".join(parts)

    def format_prompt(self, context: GenerationContext) -> str:
        """Formats GenerationContext into the final prompt string for LLMService."""
        return self._build_prompt_text(
            context.target_symbol,
            context.diff_content,
            context.source_code,
            context.dependencies,
            context.existing_test_samples,
        )
