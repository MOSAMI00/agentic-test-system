"""
Interactive Scenario Simulator for Agentic Test Generation System.
Stages real-world test cases, regression suites, and failure triage scenarios
on target repositories (e.g. D:\\Python Project or python-slugify-target)
for live execution with cognitive LLMs and Docker sandbox.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Dict, Optional, Tuple

DEFAULT_TARGET_REPO = (
    Path(r"D:\Python Project")
    if Path(r"D:\Python Project").exists()
    else Path(r"D:\the seventh term\project\mo\python-slugify-target")
)
DEFAULT_DB_PATH = Path(".agentic_test.db")
SPECIAL_PY_REL = Path("slugify/special.py")
MATH_TOOLS_REL = Path("src/math_tools.py")
README_REL = Path("README.md")


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Runs a git command in the target repository."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def reset_repo(repo: Path, verbose: bool = True) -> bool:
    """
    Restores the target repository to a pristine state.
    Discards all uncommitted changes, un-stages any staged files,
    and cleans untracked files & test caches.
    """
    if verbose:
        print(f"[*] Resetting repository at: {repo}")
    run_git(repo, "reset", "HEAD", ".")
    res1 = run_git(repo, "checkout", "--", ".")
    res2 = run_git(repo, "clean", "-fd")

    # Clean residual test/bytecode caches if any
    for cache_name in [".pytest_cache", "__pycache__", ".coverage"]:
        for cache_dir in repo.rglob(cache_name):
            try:
                if cache_dir.is_dir():
                    shutil.rmtree(cache_dir, ignore_errors=True)
                elif cache_dir.is_file():
                    cache_dir.unlink(missing_ok=True)
            except Exception:
                pass

    if res1.returncode == 0:
        if verbose:
            print("    [OK] Repository working tree is now 100% pristine.")
        return True
    if verbose:
        print(f"    [!] Git reset warning: {res1.stderr.strip()}")
    return False


def clear_database(db_path: Path = DEFAULT_DB_PATH, repo: Optional[Path] = None, verbose: bool = True) -> bool:
    """
    Clears and truncates the SQLite persistence database and journal files for a fresh demonstration run.
    Deletes all execution run histories, checkpoints, events, candidates, and triage records.
    """
    targets = [db_path]
    if repo is not None:
        target_in_repo = repo / db_path.name
        if target_in_repo != db_path and target_in_repo.exists():
            targets.append(target_in_repo)

    cleared = False
    for target in targets:
        # First, try truncating table contents if file exists
        if target.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(target))
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for t in tables:
                    conn.execute(f"DELETE FROM {t}")
                conn.commit()
                conn.execute("VACUUM")
                conn.close()
                cleared = True
            except Exception:
                pass

        # Then unlink database and WAL/SHM files
        for p in [target, Path(f"{target}-wal"), Path(f"{target}-shm")]:
            if p.exists():
                try:
                    p.unlink()
                    cleared = True
                except Exception:
                    pass

    if verbose:
        if cleared:
            print(f"    [OK] SQLite database cleared & truncated: {db_path}")
        else:
            print(f"    [*] Database {db_path} was already clean.")
    return True


def restart_code(repo: Path, clear_db: bool = True, db_path: Path = DEFAULT_DB_PATH, verbose: bool = True) -> bool:
    """
    Restores target repository to clean HEAD and clears/truncates the SQLite database.
    Cleans working tree, discards all modifications, removes untracked files and test caches,
    and truncates/deletes SQLite run history.
    """
    if verbose:
        print("\n" + "=" * 80)
        print("   [RESTART / RESET] Restoring Target Code & Database to Pristine State")
        print("=" * 80)
    ok = reset_repo(repo, verbose=verbose)
    if clear_db:
        clear_database(db_path, repo=repo, verbose=verbose)
    if verbose:
        print("   [DONE] Target code & database restored to original pristine state.\n")
    return ok


delete_scenario = restart_code


def get_base_file_content(repo: Path, rel_path: Path) -> str:
    """Fetches the original committed content of a file from HEAD."""
    res = run_git(repo, "show", f"HEAD:{rel_path.as_posix()}")
    if res.returncode != 0:
        target_file = repo / rel_path
        return target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    return res.stdout


# =============================================================================
# SCENARIO IMPLEMENTATIONS
# =============================================================================

def apply_scenario_1(repo: Path) -> None:
    """
    Scenario 1: New Uncovered Functions (Rule P4 -> ROUTE_TO_TEST_GENERATION)
    Adds 2 brand new utility functions that have zero existing tests.
    Expected:
      - Selected Route: ROUTE_TO_TEST_GENERATION
      - LLM Action: Cognitive model synthesizes pytest test candidates
      - Docker Action: Executes synthesized tests in sandbox & extracts coverage
      - Failure Diagnoses: 0
    """
    reset_repo(repo)

    if (repo / MATH_TOOLS_REL).exists():
        base_content = get_base_file_content(repo, MATH_TOOLS_REL)
        new_funcs = (
            "\n\ndef power(base: float, exponent: int) -> float:\n"
            '    """Calculates base raised to the power of exponent."""\n'
            "    return float(base ** exponent)\n\n\n"
            "def factorial(n: int) -> int:\n"
            '    """Calculates factorial of non-negative integer n."""\n'
            "    if n < 0:\n"
            '        raise ValueError("Factorial is not defined for negative numbers.")\n'
            "    if n <= 1:\n"
            "        return 1\n"
            "    result = 1\n"
            "    for i in range(2, n + 1):\n"
            "        result *= i\n"
            "    return result\n"
        )
        (repo / MATH_TOOLS_REL).write_text(base_content.rstrip() + new_funcs, encoding="utf-8")
        _print_scenario_header(
            num=1,
            title="New Uncovered Functions (Rule P4 -> ROUTE_TO_TEST_GENERATION)",
            desc="Added 'power' and 'factorial' to src/math_tools.py without existing tests.",
            expected_route="ROUTE_TO_TEST_GENERATION",
            expected_llm="Cognitive model synthesizes unit test candidates (Full synthesis).",
            expected_docker="Docker runs newly generated tests, captures coverage delta.",
            expected_diag="0 (All tests expected to pass).",
            repo_path=repo,
        )
        return

    # Fallback to slugify
    base_content = get_base_file_content(repo, SPECIAL_PY_REL)
    new_code = (
        base_content.rstrip()
        + "\n\n"
        + "def extract_language_chars(language_code: str) -> list[tuple[str, str]]:\n"
        + '    """Retrieves predefined transliteration character tuples for a given language code.\n'
        + "    :param language_code: Language identifier (e.g. 'german', 'greek', 'cyrillic').\n"
        + '    :return: List of character replacement pairs.\n'
        + '    """\n'
        + "    code = language_code.strip().lower()\n"
        + "    mapping = {\n"
        + '        "german": GERMAN,\n'
        + '        "de": GERMAN,\n'
        + '        "greek": GREEK,\n'
        + '        "el": GREEK,\n'
        + '        "cyrillic": CYRILLIC,\n'
        + '        "ru": CYRILLIC,\n'
        + "    }\n"
        + "    return list(mapping.get(code, []))\n"
        + "\n\n"
        + "def reverse_char_mapping(char_list: list[tuple[str, str]]) -> list[tuple[str, str]]:\n"
        + '    """Inverts the character mapping pairs, swapping source and replacement characters.\n'
        + "    :param char_list: List of (source, target) tuples.\n"
        + '    :return: Inverted list of (target, source) tuples.\n'
        + '    """\n'
        + "    if not char_list:\n"
        + "        return []\n"
        + "    return [(target, source) for source, target in char_list]\n"
    )
    (repo / SPECIAL_PY_REL).write_text(new_code, encoding="utf-8")
    _print_scenario_header(
        num=1,
        title="New Uncovered Functions (Rule P4 -> ROUTE_TO_TEST_GENERATION)",
        desc="Added 'extract_language_chars' & 'reverse_char_mapping' to slugify/special.py without existing tests.",
        expected_route="ROUTE_TO_TEST_GENERATION",
        expected_llm="Cognitive model synthesizes unit test candidates (Full synthesis).",
        expected_docker="Docker runs newly generated tests, captures coverage delta.",
        expected_diag="0 (All tests expected to pass).",
        repo_path=repo,
    )


def apply_scenario_2(repo: Path) -> None:
    """
    Scenario 2: Modifying Covered Code Safely (Rule P3 -> ROUTE_TO_DOCKER_EXECUTION)
    Modifies an existing function that is covered by regression tests.
    Expected:
      - Selected Route: ROUTE_TO_DOCKER_EXECUTION
      - LLM Action: ZERO LLM calls (Rule P3 Invariant INV-03)
      - Docker Action: Runs existing regression test in sandbox to verify no regressions
      - Status: COMPLETED
    """
    reset_repo(repo)

    if (repo / MATH_TOOLS_REL).exists():
        base_content = get_base_file_content(repo, MATH_TOOLS_REL)
        modified = base_content.replace(
            "def add(a: int, b: int) -> int:\n    \"\"\"Returns the sum of two integers.\"\"\"\n    return a + b",
            "def add(a: int, b: int) -> int:\n    \"\"\"Returns the sum of two integers.\"\"\"\n    # Safe internal optimization: fast integer add\n    return a + b",
        )
        (repo / MATH_TOOLS_REL).write_text(modified, encoding="utf-8")
        _print_scenario_header(
            num=2,
            title="Modifying Covered Code Safely (Rule P3 -> Zero-LLM Regression)",
            desc="Safely modified 'add' in src/math_tools.py (covered by tests/test_math_tools.py).",
            expected_route="ROUTE_TO_DOCKER_EXECUTION",
            expected_llm="ZERO LLM calls! (INV-03 Zero-LLM Gate triggers because modified symbol is covered).",
            expected_docker="Runs existing regression suite (tests/test_math_tools.py) in sandbox.",
            expected_diag="0 (Regression passes, zero failures).",
            repo_path=repo,
        )
        return

    # Fallback to slugify
    base_content = get_base_file_content(repo, SPECIAL_PY_REL)
    modified_content = base_content.replace(
        "    insertions = []",
        "    # Safe internal optimization: pre-allocated list\n    insertions: list[tuple[str, str]] = []",
    )
    (repo / SPECIAL_PY_REL).write_text(modified_content, encoding="utf-8")
    _print_scenario_header(
        num=2,
        title="Modifying Covered Code Safely (Rule P3 -> Zero-LLM Regression)",
        desc="Safely modified 'add_uppercase_char' in slugify/special.py (covered by existing test).",
        expected_route="ROUTE_TO_DOCKER_EXECUTION",
        expected_llm="ZERO LLM calls! (INV-03 Zero-LLM Gate triggers because all modified symbols are covered).",
        expected_docker="Runs existing regression suite (tests/test_add_uppercase_error.py) in sandbox.",
        expected_diag="0 (Regression passes, zero failures).",
        repo_path=repo,
    )


def apply_scenario_3(repo: Path) -> None:
    """
    Scenario 3: Application Bug Injected (Triage Diagnosis: APPLICATION_BUG)
    Introduces a defect into a covered function causing regression tests to fail.
    Expected:
      - Selected Route: ROUTE_TO_DOCKER_EXECUTION
      - Docker Action: Regression test fails with AssertionError (exit code != 0)
      - Diagnosis Action: Engine analyzes evidence, code, & diff
      - Diagnosis Result: category = APPLICATION_BUG (defect in target code logic)
    """
    reset_repo(repo)

    if (repo / MATH_TOOLS_REL).exists():
        base_content = get_base_file_content(repo, MATH_TOOLS_REL)
        buggy = base_content.replace(
            "def add(a: int, b: int) -> int:\n    \"\"\"Returns the sum of two integers.\"\"\"\n    return a + b",
            "def add(a: int, b: int) -> int:\n    \"\"\"Returns the sum of two integers.\"\"\"\n    # Injected Bug: subtraction instead of addition!\n    return a - b",
        )
        (repo / MATH_TOOLS_REL).write_text(buggy, encoding="utf-8")
        _print_scenario_header(
            num=3,
            title="Application Bug Injected (Triage: APPLICATION_BUG)",
            desc="Injected a logic bug into 'add' breaking arithmetic expectations.",
            expected_route="ROUTE_TO_DOCKER_EXECUTION",
            expected_llm="Cognitive Triage analyzes failure traceback and Git Diff.",
            expected_docker="Regression test fails with AssertionError (Exit Code: 1).",
            expected_diag="1 Failure Diagnosis -> Category: APPLICATION_BUG.",
            repo_path=repo,
        )
        return

    # Fallback to slugify
    base_content = get_base_file_content(repo, SPECIAL_PY_REL)
    buggy_content = base_content.replace(
        "        upper_dict = char.upper(), xlate.capitalize()",
        "        # Injected Bug: inverted case logic!\n        upper_dict = char.lower(), xlate.lower()",
    )
    (repo / SPECIAL_PY_REL).write_text(buggy_content, encoding="utf-8")
    _print_scenario_header(
        num=3,
        title="Application Bug Injected (Triage: APPLICATION_BUG)",
        desc="Injected a logic bug into 'add_uppercase_char' breaking uppercase capitalization.",
        expected_route="ROUTE_TO_DOCKER_EXECUTION",
        expected_llm="Cognitive Triage analyzes failure traceback and Git Diff.",
        expected_docker="Regression test fails with AssertionError (Exit Code: 1).",
        expected_diag="1 Failure Diagnosis -> Category: APPLICATION_BUG.",
        repo_path=repo,
    )


def apply_scenario_4(repo: Path) -> None:
    """
    Scenario 4: Intentional Contract Change (Triage Diagnosis: TEST_OUTDATED)
    Updates contract/signature and docstring intentionally.
    Expected:
      - Selected Route: ROUTE_TO_DOCKER_EXECUTION
      - Docker Action: Old regression test fails because it expects old return type
      - Diagnosis Action: Cognitive Triage reads docstring and diff indicating intentional change
      - Diagnosis Result: category = TEST_OUTDATED
    """
    reset_repo(repo)

    if (repo / MATH_TOOLS_REL).exists():
        base_content = get_base_file_content(repo, MATH_TOOLS_REL)
        contract_change = (
            'def add(a: int, b: int) -> dict[str, int]:\n'
            '    """Breaking contract change (v2.0): Function returns a structured dictionary\n'
            '    containing the sum under the "result" key instead of raw integer."""\n'
            '    return {"result": a + b}'
        )
        modified = base_content.replace(
            "def add(a: int, b: int) -> int:\n    \"\"\"Returns the sum of two integers.\"\"\"\n    return a + b",
            contract_change,
        )
        (repo / MATH_TOOLS_REL).write_text(modified, encoding="utf-8")
        _print_scenario_header(
            num=4,
            title="Intentional Contract Change (Triage: TEST_OUTDATED)",
            desc="Updated 'add' contract in v2.0 to return dict[str, int] instead of int.",
            expected_route="ROUTE_TO_DOCKER_EXECUTION",
            expected_llm="Cognitive Triage detects that contract was intentionally updated in the diff.",
            expected_docker="Old regression test fails on old expectations (Exit Code: 1).",
            expected_diag="1 Failure Diagnosis -> Category: TEST_OUTDATED.",
            repo_path=repo,
        )
        return

    # Fallback to slugify
    base_content = get_base_file_content(repo, SPECIAL_PY_REL)
    target_block = (
        'def add_uppercase_char(char_list: list[tuple[str, str]]) -> list[tuple[str, str]]:\n'
        '    """ Given a replacement char list, this adds uppercase chars to the list """'
    )
    contract_change = (
        'def add_uppercase_char(char_list: list[tuple[str, str]]) -> list[tuple[str, str]]:\n'
        '    """Breaking contract change (v10.0): Function returns a new list with uppercase pairs\n'
        '    appended at the end to preserve input order, rather than prepending in-place."""\n'
        '    result = list(char_list)\n'
        '    for item in char_list:\n'
        '        char, xlate = item\n'
        '        upper_pair = (char.upper(), xlate.capitalize())\n'
        '        if upper_pair not in result and char != upper_pair[0]:\n'
        '            result.append(upper_pair)\n'
        '    return result'
    )
    old_func_end = base_content.find("# Language specific pre translations")
    if old_func_end != -1:
        new_content = contract_change + "\n\n\n" + base_content[old_func_end:]
    else:
        new_content = base_content.replace(target_block, contract_change)
    (repo / SPECIAL_PY_REL).write_text(new_content, encoding="utf-8")
    _print_scenario_header(
        num=4,
        title="Intentional Contract Change (Triage: TEST_OUTDATED)",
        desc="Updated 'add_uppercase_char' contract in v10.0 to append uppercase characters instead of prepending in-place.",
        expected_route="ROUTE_TO_DOCKER_EXECUTION",
        expected_llm="Cognitive Triage detects that contract was intentionally updated in the diff.",
        expected_docker="Old regression test fails on old expectations (Exit Code: 1).",
        expected_diag="1 Failure Diagnosis -> Category: TEST_OUTDATED.",
        repo_path=repo,
    )


def apply_scenario_5(repo: Path) -> None:
    """
    Scenario 5: Deleted Function with Existing Tests (Decision 1 Case B)
    Deletes a function from source code while regression tests still import it.
    Expected:
      - Selected Route: ROUTE_TO_DOCKER_EXECUTION (Decision 1 Case B)
      - LLM Action: Zero generation (INV-03 forbids generating tests for deleted code)
      - Docker Action: Pytest runs regression test and fails with ImportError
      - Diagnosis Action: Failure diagnosed as breaking deletion regression
    """
    reset_repo(repo)

    if (repo / MATH_TOOLS_REL).exists():
        base_content = get_base_file_content(repo, MATH_TOOLS_REL)
        target_func = (
            "def add(a: int, b: int) -> int:\n"
            '    """Returns the sum of two integers."""\n'
            "    return a + b\n\n\n"
        )
        deleted_content = base_content.replace(target_func, "")
        (repo / MATH_TOOLS_REL).write_text(deleted_content, encoding="utf-8")
        _print_scenario_header(
            num=5,
            title="Deleted Function with Tests (Decision 1 Case B)",
            desc="Completely deleted 'add' from src/math_tools.py (tests/test_math_tools.py still imports it).",
            expected_route="ROUTE_TO_DOCKER_EXECUTION",
            expected_llm="Zero generation! Tests cannot be generated for deleted callables.",
            expected_docker="Regression test fails with ImportError / AttributeError.",
            expected_diag="Diagnosed as regression failure caused by symbol deletion.",
            repo_path=repo,
        )
        return

    # Fallback to slugify
    base_content = get_base_file_content(repo, SPECIAL_PY_REL)
    split_pos = base_content.find("# Language specific pre translations")
    if split_pos != -1:
        imports = "from __future__ import annotations\n\n\n"
        new_content = imports + base_content[split_pos:]
    else:
        new_content = base_content
    (repo / SPECIAL_PY_REL).write_text(new_content, encoding="utf-8")
    _print_scenario_header(
        num=5,
        title="Deleted Function with Tests (Decision 1 Case B)",
        desc="Completely deleted 'add_uppercase_char' from slugify/special.py (existing tests still import it).",
        expected_route="ROUTE_TO_DOCKER_EXECUTION",
        expected_llm="Zero generation! Tests cannot be generated for deleted callables.",
        expected_docker="Regression test fails with ImportError / ModuleAttributeError.",
        expected_diag="Diagnosed as regression failure caused by symbol deletion.",
        repo_path=repo,
    )


def apply_scenario_6(repo: Path) -> None:
    """
    Scenario 6: Non-Code Modifications Only (Rule P2 -> ROUTE_NO_OP)
    Modifies README.md documentation only without altering Python code.
    Expected:
      - Selected Route: ROUTE_NO_OP (Rule P2)
      - LLM Action: 0 calls
      - Docker Action: 0 runs
      - Final Status: COMPLETED
    """
    reset_repo(repo)
    readme = repo / README_REL
    if readme.exists():
        content = readme.read_text(encoding="utf-8")
        readme.write_text(content + "\n<!-- Live Scenario Documentation Update -->\n", encoding="utf-8")
    _print_scenario_header(
        num=6,
        title="Non-Code Modification Only (Rule P2 -> ROUTE_NO_OP)",
        desc="Added documentation comment to README.md without touching any Python files.",
        expected_route="ROUTE_NO_OP",
        expected_llm="ZERO LLM calls (Rule P2 skips non-code changes).",
        expected_docker="ZERO Docker executions.",
        expected_diag="0 Diagnoses (Immediate successful completion).",
        repo_path=repo,
    )


def check_environment() -> Tuple[bool, str]:
    """Checks the status of the local Docker daemon and configured LLM provider."""
    # Docker check
    docker_ready = False
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=3)
        docker_ready = (proc.returncode == 0)
    except Exception:
        docker_ready = False

    # LLM check
    if os.environ.get("GEMINI_API_KEY"):
        llm_desc = "Gemini API (GEMINI_API_KEY)"
    elif os.environ.get("OPENAI_API_KEY"):
        llm_desc = "OpenAI API (OPENAI_API_KEY)"
    elif os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AGENTIC_TEST_OPENROUTER_API_KEY"):
        llm_desc = "OpenRouter API"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        llm_desc = "Anthropic Claude API"
    else:
        llm_desc = "None (Offline mode available)"

    return docker_ready, llm_desc


def show_git_status_and_diff(repo: Path) -> None:
    """Displays git status and git diff for target repository to show the committee."""
    print("\n" + "=" * 80)
    print("   [GIT STATUS & DIFF]")
    print(f"   Target Repository: {repo}")
    print("=" * 80)

    status_proc = run_git(repo, "status", "-s")
    if status_proc.stdout.strip():
        print("  Staged / Unstaged Files:")
        for line in status_proc.stdout.strip().splitlines():
            print(f"    {line}")
    else:
        print("  Working tree is clean. (No changes staged).")

    print("-" * 80)
    diff_proc = run_git(repo, "diff")
    if diff_proc.stdout.strip():
        print("  Git Diff:")
        for line in diff_proc.stdout.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                print(f"  \033[92m{line}\033[0m")
            elif line.startswith("-") and not line.startswith("---"):
                print(f"  \033[91m{line}\033[0m")
            elif line.startswith("@@"):
                print(f"  \033[96m{line}\033[0m")
            else:
                print(f"  {line}")
    else:
        print("  (Diff is empty - no code modifications active).")
    print("=" * 80 + "\n")


def execute_pipeline(repo: Path, offline: bool = False, db_path: Path = DEFAULT_DB_PATH) -> int:
    """
    Executes the Agentic Test System pipeline on the target repository using `agentic-test run`.
    Streams rich console output in real-time.
    """
    agentic_exe = shutil.which("agentic-test")
    if agentic_exe:
        cmd = [agentic_exe, "run", "--repo", str(repo), "--db", str(db_path)]
        display_cmd = f'agentic-test run --repo "{repo}"'
    else:
        cmd = [sys.executable, "-m", "agentic_test.cli", "run", "--repo", str(repo), "--db", str(db_path)]
        display_cmd = f'python -m agentic_test.cli run --repo "{repo}"'

    if offline:
        cmd.append("--offline")
        display_cmd += " --offline"

    mode_label = "OFFLINE MOCK MODE (Hermetic)" if offline else "LIVE MODE (Docker + Real LLM)"
    print("\n" + "=" * 80)
    print(f"   [LAUNCHING PIPELINE] -> {mode_label}")
    print(f"   Command: {display_cmd}")
    print("=" * 80 + "\n")

    # On Windows, executing .exe/.bat through shell=True preserves full ANSI Rich rendering
    proc = subprocess.run(cmd, shell=bool(agentic_exe))
    if proc.returncode == 0:
        print("\n[OK] Pipeline completed successfully.")
    else:
        print(f"\n[!] Pipeline finished with exit code: {proc.returncode}")
    return proc.returncode


def restart_scenario(
    repo: Path,
    scenario_num: int,
    offline: bool = False,
    auto_run: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """
    Restarts a scenario: cleanly resets the repository and re-applies the scenario from scratch.
    Optionally executes the pipeline right away.
    """
    print("\n" + "=" * 80)
    print(f"   [RESTARTING SCENARIO {scenario_num}]")
    print("=" * 80)
    reset_repo(repo, verbose=True)
    if scenario_num in SCENARIOS:
        _, fn = SCENARIOS[scenario_num]
        fn(repo)
        if auto_run:
            execute_pipeline(repo, offline=offline, db_path=db_path)
    else:
        print(f"[!] Unknown scenario number: {scenario_num}")


def _print_scenario_header(
    num: int,
    title: str,
    desc: str,
    expected_route: str,
    expected_llm: str,
    expected_docker: str,
    expected_diag: str,
    repo_path: Optional[Path] = None,
) -> None:
    sep = "=" * 80
    target_repo = repo_path or DEFAULT_TARGET_REPO
    print(f"\n{sep}")
    print(f"  [SCENARIO {num} APPLIED SUCCESSFULLY]")
    print(f"  Title: {title}")
    print(f"{sep}")
    print(f"  * Target Repo:       {target_repo}")
    print(f"  * Description:       {desc}")
    print(f"  * Expected Route:    {expected_route}")
    print(f"  * Expected LLM:      {expected_llm}")
    print(f"  * Expected Sandbox:  {expected_docker}")
    print(f"  * Expected Triage:   {expected_diag}")
    print(f"{sep}")
    print("  [>] Ready to test! Run this exact command in PowerShell:")
    print()
    print(f'      agentic-test run --repo "{target_repo}"')
    print()
    print("  Or run it directly from this console using option [x] or prompt below.")
    print(f"{sep}\n")


def print_presentation_guide() -> None:
    """Prints a structured architectural defense guide for presentation before the committee."""
    print("\n" + "=" * 85)
    print("   PROJECT DEFENSE & EVALUATION GUIDE")
    print("=" * 85)
    guides = [
        (
            "Scenario 1: New Uncovered Functions",
            "Rule P4 -> ROUTE_TO_TEST_GENERATION",
            "Adds 'power' and 'factorial'. Proves the system discovers coverage gaps and synthesizes new unit tests with LLM.",
            "Synthesizes pytest candidates, passes validation, sandbox executes tests, coverage delta extracted.",
        ),
        (
            "Scenario 2: Safe Covered Modification",
            "Rule P3 -> ROUTE_TO_DOCKER_EXECUTION (Zero-LLM)",
            "Modifies 'add' with safe optimization. Proves Safety Invariant INV-03: Zero LLM cost when existing regression covers change.",
            "ZERO LLM calls! Sandbox immediately runs existing tests/test_math_tools.py, passes with 0 failures.",
        ),
        (
            "Scenario 3: Application Bug Injected",
            "Triage -> APPLICATION_BUG Defect Detection",
            "Injects 'return a - b' in 'add'. Proves safety invariant: The system NEVER mutates tests to hide genuine bugs in application code.",
            "Pytest fails in Docker with AssertionError. LLM Triage analyzes traceback and diff, flags APPLICATION_BUG with high confidence.",
        ),
        (
            "Scenario 4: Intentional Contract Change",
            "Triage -> TEST_OUTDATED (Signature Evolution)",
            "Changes 'add' return type to dict[str, int]. Proves system differentiates between bugs and intentional API changes.",
            "Old test fails. Cognitive triage analyzes updated docstring and diff, correctly classifies failure as TEST_OUTDATED.",
        ),
        (
            "Scenario 5: Deleted Covered Function",
            "Decision 1 Case B -> Deletion Regression",
            "Deletes 'add' while tests still import it. Proves system does NOT generate tests for deleted code and catches regression.",
            "Zero generation! Tests fail with ImportError / AttributeError in sandbox. Diagnosed as breaking deletion.",
        ),
        (
            "Scenario 6: Documentation Only",
            "Rule P2 -> ROUTE_NO_OP (Fast Bypass)",
            "Modifies README.md only. Proves zero-resource bypass for non-code files.",
            "Zero LLM calls, zero Docker executions. System completes immediately without wasting tokens or CPU.",
        ),
    ]

    for title, route, objective, outcome in guides:
        print(f"\n  * {title}")
        print(f"    - Route:              {route}")
        print(f"    - Objective:          {objective}")
        print(f"    - Expected Outcome:   {outcome}")

    print("\n" + "=" * 85 + "\n")


# =============================================================================
# CLI INTERACTION
# =============================================================================

SCENARIOS = {
    1: ("New Uncovered Functions -> Test Generation (Rule P4)", apply_scenario_1),
    2: ("Modify Covered Function -> Safe Regression Suite (Zero-LLM Rule P3)", apply_scenario_2),
    3: ("Inject Application Bug -> Docker Failure & LLM Diagnosis (APPLICATION_BUG)", apply_scenario_3),
    4: ("Contract Change -> Regression Failure & LLM Diagnosis (TEST_OUTDATED)", apply_scenario_4),
    5: ("Delete Covered Function -> Breaking Deletion (Decision 1 Case B)", apply_scenario_5),
    6: ("Documentation Only -> Clean Non-Code Bypass (Rule P2: ROUTE_NO_OP)", apply_scenario_6),
}


def print_menu(repo: Path, active_scenario: Optional[int], docker_ready: bool, llm_desc: str) -> None:
    docker_badge = "[ONLINE]" if docker_ready else "[OFFLINE (Docker Desktop off)]"
    active_label = (
        f"[{active_scenario}] {SCENARIOS[active_scenario][0]}"
        if active_scenario in SCENARIOS
        else "[None - Working tree clean]"
    )

    print("\n" + "=" * 80)
    print("   Agentic Test Generation & Maintenance System - Defense Simulation Console")
    print("=" * 80)
    print(f"  Target Repository : {repo}")
    print(f"  Active Scenario   : {active_label}")
    print(f"  Docker Status     : {docker_badge}")
    print(f"  LLM Provider      : {llm_desc}")
    print("-" * 80)
    print("  Available Scenarios:")
    for key, (label, _) in SCENARIOS.items():
        marker = ">> " if key == active_scenario else "   "
        print(f"  {marker}[{key}] {label}")
    print("-" * 80)
    print("  Presentation Controls:")
    print("    [x] Run Pipeline Now          (Execute agentic test pipeline on staged scenario)")
    print("    [r] Restart Code & DB         (Restore target repo to clean HEAD & truncate DB)")
    print("    [c] Truncate SQLite DB        (Truncate and clear all database records only)")
    print("    [s] Show Git Diff & Status    (Display staged git diff and modified files)")
    print("    [h] Defense Guide & Details   (Display architectural rationale for each scenario)")
    print("    [q] Quit                      (Exit console)")
    print("=" * 80)


def prompt_run_decision(repo: Path, docker_ready: bool, db_path: Path) -> None:
    """Prompts the user whether to execute the pipeline right after staging a scenario."""
    default_opt = "y" if docker_ready else "o"
    prompt_str = f"  >> Run pipeline now? [y: Live (Docker+LLM) / o: Offline / n: Later] (default: {default_opt}): "
    try:
        ans = input(prompt_str).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if not ans:
        ans = default_opt

    if ans in ("y", "yes", "live", "1"):
        if not docker_ready:
            print("  [!] Warning: Docker is not reachable. Falling back to offline mode.")
            execute_pipeline(repo, offline=True, db_path=db_path)
        else:
            execute_pipeline(repo, offline=False, db_path=db_path)
    elif ans in ("o", "offline", "mock", "2"):
        execute_pipeline(repo, offline=True, db_path=db_path)
    else:
        print("  [*] Scenario staged. Ready to run anytime via option [x].")


def interactive_loop(repo: Path, db_path: Path) -> None:
    """Main interactive presentation loop."""
    active_scenario: Optional[int] = None

    while True:
        docker_ready, llm_desc = check_environment()
        print_menu(repo, active_scenario, docker_ready, llm_desc)

        try:
            choice = input("Enter choice [1-6, x, r, c, s, h, q]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting presentation console. Goodbye!")
            break

        if choice in ("q", "quit", "exit"):
            print("\nExiting presentation console. Good luck with your defense!")
            break

        if choice in ("0", "r", "d", "restart", "reset", "delete"):
            # Restart / Reset target code back to original pristine state & truncate DB
            restart_code(repo, clear_db=True, db_path=db_path, verbose=True)
            active_scenario = None
            continue

        if choice in ("x", "run", "exec"):
            # Run pipeline now
            prompt_run_decision(repo, docker_ready, db_path)
            continue

        if choice in ("s", "diff", "status"):
            # Show git diff & status
            show_git_status_and_diff(repo)
            try:
                input("Press Enter to return to menu...")
            except (KeyboardInterrupt, EOFError):
                pass
            continue

        if choice in ("c", "clear", "clear-db", "truncate", "truncate-db"):
            # Truncate / Clear database
            clear_database(db_path, repo=repo, verbose=True)
            try:
                input("Press Enter to return to menu...")
            except (KeyboardInterrupt, EOFError):
                pass
            continue

        if choice in ("h", "help", "guide"):
            # Show defense presentation guide
            print_presentation_guide()
            try:
                input("Press Enter to return to menu...")
            except (KeyboardInterrupt, EOFError):
                pass
            continue

        # Handle numeric scenario selection [1-6]
        try:
            val = int(choice)
            if val in SCENARIOS:
                _, fn = SCENARIOS[val]
                fn(repo)
                active_scenario = val
                prompt_run_decision(repo, docker_ready, db_path)
            else:
                print(f"[!] Invalid scenario number: {val}. Choose [1-6].")
        except ValueError:
            print(f"[!] Unrecognized command: '{choice}'. Enter 1-6, x, r, c, s, h, or q.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live Scenario Simulator & Defense Presentation Console for Agentic Test System."
    )
    parser.add_argument("--repo", type=Path, default=DEFAULT_TARGET_REPO, help="Path to target repository.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to SQLite database.")
    parser.add_argument(
        "--scenario",
        "-s",
        type=int,
        choices=list(SCENARIOS.keys()) + [0],
        help="Scenario number to apply directly (1-6, or 0 for reset).",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Immediately execute the agentic test pipeline after applying scenario.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Execute pipeline in hermetic offline mode without Docker or external APIs.",
    )
    parser.add_argument(
        "--restart",
        "-r",
        "--reset",
        action="store_true",
        help="Restart / Reset: restore target code and truncate SQLite database to pristine state.",
    )
    parser.add_argument(
        "--delete",
        "-d",
        action="store_true",
        help="Alias for --restart / --reset: restore target repository and truncate database.",
    )
    parser.add_argument(
        "--clear-db",
        "--truncate-db",
        action="store_true",
        help="Truncate/clear all records in the SQLite persistence database.",
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="Keep SQLite database intact during restart/reset without truncating.",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Display the git status and git diff in the target repository.",
    )
    parser.add_argument(
        "--guide",
        action="store_true",
        help="Print the evaluation and defense presentation guide for the committee.",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Force launching interactive menu even if flags are passed.",
    )
    args = parser.parse_args()

    repo = args.repo.resolve()
    if not repo.exists() or not repo.is_dir():
        print(f"Error: Target repository does not exist: {repo}")
        sys.exit(1)

    db_path = args.db.resolve()

    # Direct action flags:
    # 1. Restart / Reset: Restore target code back to pristine original state & truncate database
    if args.restart or args.delete or (args.scenario == 0):
        should_clear = not args.keep_db
        restart_code(repo, clear_db=should_clear, db_path=db_path)
        if not args.interactive:
            return

    # 2. Clear / Truncate Database flag
    if args.clear_db:
        clear_database(db_path, repo=repo)
        if not args.interactive and args.scenario is None:
            return

    # 3. Diff flag
    if args.diff:
        show_git_status_and_diff(repo)
        if not args.interactive and args.scenario is None:
            return

    # 4. Guide flag
    if args.guide:
        print_presentation_guide()
        if not args.interactive and args.scenario is None:
            return

    # 5. Apply Scenario flag (1-6)
    if args.scenario is not None and args.scenario in SCENARIOS:
        _, fn = SCENARIOS[args.scenario]
        fn(repo)
        if args.run:
            execute_pipeline(repo, offline=args.offline, db_path=db_path)
        if not args.interactive:
            return

    # If no flags (or --interactive), launch interactive presentation console
    interactive_loop(repo, db_path)


if __name__ == "__main__":
    main()

