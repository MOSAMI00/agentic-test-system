"""
Interactive Scenario Simulator for Agentic Test Generation System.
Stages real-world test cases, regression suites, and failure triage scenarios
on the target repository (D:\\the seventh term\\project\\mo\\python-slugify-target)
for live execution with real LLM (Gemini) and Docker sandbox.
"""

import argparse
from pathlib import Path
import subprocess
import sys

DEFAULT_TARGET_REPO = Path(r"D:\the seventh term\project\mo\python-slugify-target")
SPECIAL_PY_REL = Path("slugify/special.py")
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


def reset_repo(repo: Path) -> bool:
    """Restores the target repository to a pristine state."""
    print(f"[*] Resetting repository at: {repo}")
    res1 = run_git(repo, "checkout", "--", ".")
    res2 = run_git(repo, "clean", "-fd")
    if res1.returncode == 0:
        print("    [✓] Repository working tree is now clean.")
        return True
    print(f"    [!] Git reset warning: {res1.stderr.strip()}")
    return False


def get_base_special_content(repo: Path) -> str:
    """Fetches the original committed content of slugify/special.py from HEAD."""
    res = run_git(repo, "show", "HEAD:slugify/special.py")
    if res.returncode != 0:
        # Fallback reading current file
        target_file = repo / SPECIAL_PY_REL
        return target_file.read_text(encoding="utf-8")
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
      - LLM Action: Gemini synthesizes pytest test candidates
      - Docker Action: Executes synthesized tests in sandbox & extracts coverage
      - Failure Diagnoses: 0
    """
    reset_repo(repo)
    base_content = get_base_special_content(repo)
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
        expected_llm="Gemini generates unit test candidates (Full synthesis).",
        expected_docker="Docker runs newly generated tests, captures coverage delta.",
        expected_diag="0 (All tests expected to pass).",
    )


def apply_scenario_2(repo: Path) -> None:
    """
    Scenario 2: Modifying Covered Code Safely (Rule P3 -> ROUTE_TO_DOCKER_EXECUTION)
    Modifies existing function 'add_uppercase_char' which is covered by tests/test_add_uppercase_error.py.
    Expected:
      - Selected Route: ROUTE_TO_DOCKER_EXECUTION
      - LLM Action: ZERO LLM calls (Rule P3 Invariant INV-03)
      - Docker Action: Runs existing regression test in sandbox to verify no regressions
      - Status: COMPLETED
    """
    reset_repo(repo)
    base_content = get_base_special_content(repo)
    # Add a harmless internal optimization/comment to add_uppercase_char
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
    )


def apply_scenario_3(repo: Path) -> None:
    """
    Scenario 3: Application Bug Injected (Triage Diagnosis: APPLICATION_BUG)
    Introduces a defect into 'add_uppercase_char' causing regression test to fail.
    Expected:
      - Selected Route: ROUTE_TO_DOCKER_EXECUTION
      - Docker Action: Regression test fails with AssertionError (exit code != 0)
      - Diagnosis Action: Engine 2 (CognitiveLLMClassifier) analyzes evidence, code, & diff
      - Diagnosis Result: category = APPLICATION_BUG (defect in target code logic)
    """
    reset_repo(repo)
    base_content = get_base_special_content(repo)
    # Inject a logic bug: convert to lower instead of upper and capitalize!
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
    )


def apply_scenario_4(repo: Path) -> None:
    """
    Scenario 4: Intentional Contract Change (Triage Diagnosis: TEST_OUTDATED)
    Updates 'add_uppercase_char' contract/signature and docstring intentionally.
    Expected:
      - Selected Route: ROUTE_TO_DOCKER_EXECUTION
      - Docker Action: Old regression test fails because it expects old return type
      - Diagnosis Action: CognitiveLLMClassifier reads docstring and diff indicating intentional change
      - Diagnosis Result: category = TEST_OUTDATED
    """
    reset_repo(repo)
    base_content = get_base_special_content(repo)
    # Replace add_uppercase_char with an intentional breaking contract change
    target_block = (
        'def add_uppercase_char(char_list: list[tuple[str, str]]) -> list[tuple[str, str]]:\n'
        '    """ Given a replacement char list, this adds uppercase chars to the list """'
    )
    contract_change = (
        'def add_uppercase_char(char_list: list[tuple[str, str]], as_dict: bool = True):\n'
        '    """Intentional breaking change: Contract updated in v10.0 to return dict mapping."""\n'
        '    return {c: x.upper() for c, x in char_list}'
    )
    # Truncate original function body
    old_func_end = base_content.find("# Language specific pre translations")
    if old_func_end != -1:
        new_content = contract_change + "\n\n\n" + base_content[old_func_end:]
    else:
        new_content = base_content.replace(target_block, contract_change)
    (repo / SPECIAL_PY_REL).write_text(new_content, encoding="utf-8")
    _print_scenario_header(
        num=4,
        title="Intentional Contract Change (Triage: TEST_OUTDATED)",
        desc="Updated 'add_uppercase_char' signature and contract to return a dictionary instead of a list.",
        expected_route="ROUTE_TO_DOCKER_EXECUTION",
        expected_llm="Cognitive Triage detects that contract was intentionally updated in the diff.",
        expected_docker="Old regression test fails on old expectations.",
        expected_diag="1 Failure Diagnosis -> Category: TEST_OUTDATED.",
    )


def apply_scenario_5(repo: Path) -> None:
    """
    Scenario 5: Deleted Function with Existing Tests (Decision 1 Case B)
    Deletes 'add_uppercase_char' from source code while tests/test_add_uppercase_error.py still exists.
    Expected:
      - Selected Route: ROUTE_TO_DOCKER_EXECUTION (Decision 1 Case B)
      - LLM Action: Zero generation (INV-03 forbids generating tests for deleted code)
      - Docker Action: Pytest runs regression test and fails with ImportError
      - Diagnosis Action: Failure diagnosed as breaking deletion regression
    """
    reset_repo(repo)
    base_content = get_base_special_content(repo)
    # Delete the add_uppercase_char function completely
    split_pos = base_content.find("# Language specific pre translations")
    if split_pos != -1:
        # Keep imports and rest of file, but remove add_uppercase_char
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
    )


def _print_scenario_header(
    num: int,
    title: str,
    desc: str,
    expected_route: str,
    expected_llm: str,
    expected_docker: str,
    expected_diag: str,
) -> None:
    sep = "=" * 80
    print(f"\n{sep}")
    print(f"  [SCENARIO {num} APPLIED SUCCESSFULLY]")
    print(f"  Title: {title}")
    print(f"{sep}")
    print(f"  * Description:       {desc}")
    print(f"  * Expected Route:    {expected_route}")
    print(f"  * Expected LLM:      {expected_llm}")
    print(f"  * Expected Sandbox:  {expected_docker}")
    print(f"  * Expected Triage:   {expected_diag}")
    print(f"{sep}")
    print("  [>] Ready to test! Run this exact command in PowerShell:")
    print()
    print('      .\\.venv\\Scripts\\python.exe -m agentic_test.cli run --repo "D:\\the seventh term\\project\\mo\\python-slugify-target"')
    print(f"{sep}\n")


# =============================================================================
# CLI INTERACTION
# =============================================================================

SCENARIOS = {
    1: ("New Uncovered Functions -> Test Generation", apply_scenario_1),
    2: ("Modify Covered Function -> Safe Regression Suite (Zero-LLM)", apply_scenario_2),
    3: ("Inject Application Bug -> Docker Failure & LLM Diagnosis (APPLICATION_BUG)", apply_scenario_3),
    4: ("Contract Change -> Regression Failure & LLM Diagnosis (TEST_OUTDATED)", apply_scenario_4),
    5: ("Delete Covered Function -> Breaking Deletion Regression (Decision 1 Case B)", apply_scenario_5),
    6: ("Documentation Only -> Clean Non-Code Bypass (Rule P2: ROUTE_NO_OP)", apply_scenario_6),
}


def print_menu() -> None:
    print("\n" + "=" * 75)
    print("   Agentic Test System - Live Scenario Simulator (Real LLM & Docker)")
    print("=" * 75)
    print(f"Target Repository: {DEFAULT_TARGET_REPO}\n")
    print("Select a scenario to prepare in the target repository:")
    for key, (label, _) in SCENARIOS.items():
        print(f"  [{key}] {label}")
    print("  [0] Reset repository to pristine git HEAD")
    print("  [q] Quit")
    print("=" * 75)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Scenario Simulator for Agentic Test System.")
    parser.add_argument("--repo", type=Path, default=DEFAULT_TARGET_REPO, help="Path to target repository.")
    parser.add_argument("--scenario", "-s", type=int, choices=list(SCENARIOS.keys()) + [0], help="Scenario number to apply directly.")
    parser.add_argument("--reset", "-r", action="store_true", help="Reset target repo to pristine state.")
    args = parser.parse_args()

    repo = args.repo.resolve()
    if not repo.exists() or not repo.is_dir():
        print(f"Error: Target repository does not exist: {repo}")
        sys.exit(1)

    if args.reset:
        reset_repo(repo)
        return

    if args.scenario is not None:
        if args.scenario == 0:
            reset_repo(repo)
        else:
            _, fn = SCENARIOS[args.scenario]
            fn(repo)
        return

    # Interactive loop
    while True:
        print_menu()
        try:
            choice = input("Enter choice [0-6, q]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if choice in ("q", "quit", "exit"):
            break
        if choice == "0":
            reset_repo(repo)
            continue

        try:
            val = int(choice)
            if val in SCENARIOS:
                _, fn = SCENARIOS[val]
                fn(repo)
            else:
                print("[!] Invalid scenario number.")
        except ValueError:
            print("[!] Please enter a valid number.")


if __name__ == "__main__":
    main()
