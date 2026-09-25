"""
Unit tests for TestDiscovery service and static symbol association analysis.
Stage 3 Section 4.4.4.1.
Enforces INV-01, INV-02, and conservative static evidence gating.
"""

from pathlib import Path

from agentic_test.analysis.discovery import (
    AssociationClassification,
    EvidenceKind,
    TestDiscovery,
)
from agentic_test.core.models import SymbolContract, SymbolType


def test_discover_test_suites(tmp_path: Path) -> None:
    # Create directory structure with test and non-test files
    (tmp_path / "test_a.py").write_text("def test_one(): pass\n", encoding="utf-8")
    (tmp_path / "b_test.py").write_text("def test_two(): pass\n", encoding="utf-8")
    (tmp_path / "normal_module.py").write_text("def helper(): pass\n", encoding="utf-8")

    # Excluded directories
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "test_in_venv.py").write_text("def test_venv(): pass\n", encoding="utf-8")

    discovery = TestDiscovery()
    suites = discovery.discover_test_suites(tmp_path)

    suite_names = [p.name for p in suites]
    assert "test_a.py" in suite_names
    assert "b_test.py" in suite_names
    assert "normal_module.py" not in suite_names
    assert "test_in_venv.py" not in suite_names


def test_extract_tested_symbols_unconfigured_returns_empty_list(tmp_path: Path) -> None:
    test_file = tmp_path / "test_calc.py"
    test_file.write_text("from calc import add\ndef test_add(): assert add(1, 2) == 3\n", encoding="utf-8")

    # Unconfigured TestDiscovery must not guess; returns []
    discovery = TestDiscovery()
    assert discovery.extract_tested_symbols(test_file) == []


def test_association_direct_imported_call(tmp_path: Path) -> None:
    test_file = tmp_path / "test_math.py"
    test_file.write_text(
        "from math_ops import add\n"
        "\n"
        "def test_add_nominal():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="math_ops.add",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("math_ops.py"),
        line_range=(1, 3),
        signature="def add(a, b)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assoc = associations[0]
    assert assoc.target_symbol == "math_ops.add"
    assert assoc.classification == AssociationClassification.STATICALLY_ASSOCIATED
    assert assoc.evidence_kind == EvidenceKind.DIRECT_CALL
    assert assoc.test_case_name == "test_add_nominal"
    assert assoc.call_line_number == 4
    assert assoc.is_name_correlated is True

    # extract_tested_symbols returns the qualified name
    assert discovery.extract_tested_symbols(test_file) == ["math_ops.add"]


def test_association_module_qualified_call(tmp_path: Path) -> None:
    test_file = tmp_path / "test_math.py"
    test_file.write_text(
        "import math_ops\n"
        "\n"
        "def test_sub():\n"
        "    res = math_ops.sub(5, 2)\n"
        "    assert res == 3\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="math_ops.sub",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("math_ops.py"),
        line_range=(5, 8),
        signature="def sub(a, b)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assoc = associations[0]
    assert assoc.classification == AssociationClassification.STATICALLY_ASSOCIATED
    assert assoc.evidence_kind == EvidenceKind.MODULE_QUALIFIED_CALL
    assert assoc.call_line_number == 4
    assert discovery.extract_tested_symbols(test_file) == ["math_ops.sub"]


def test_association_aliased_call(tmp_path: Path) -> None:
    test_file = tmp_path / "test_math.py"
    test_file.write_text(
        "from math_ops import multiply as my_mul\n"
        "\n"
        "def test_multiply():\n"
        "    assert my_mul(2, 4) == 8\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="math_ops.multiply",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("math_ops.py"),
        line_range=(10, 15),
        signature="def multiply(a, b)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assoc = associations[0]
    assert assoc.classification == AssociationClassification.STATICALLY_ASSOCIATED
    assert assoc.evidence_kind == EvidenceKind.ALIASED_CALL
    assert discovery.extract_tested_symbols(test_file) == ["math_ops.multiply"]


def test_association_locally_instantiated_method_call(tmp_path: Path) -> None:
    test_file = tmp_path / "test_calc.py"
    test_file.write_text(
        "from calc import Calculator\n"
        "\n"
        "def test_clear():\n"
        "    c = Calculator()\n"
        "    c.clear()\n",
        encoding="utf-8",
    )

    cls_sym = SymbolContract(
        qualified_name="calc.Calculator",
        symbol_type=SymbolType.CLASS,
        file_path=Path("calc.py"),
        line_range=(1, 10),
        signature="class Calculator",
    )
    method_sym = SymbolContract(
        qualified_name="calc.Calculator.clear",
        symbol_type=SymbolType.METHOD,
        file_path=Path("calc.py"),
        line_range=(5, 8),
        signature="def clear(self)",
    )

    discovery = TestDiscovery(target_symbols=[cls_sym, method_sym])
    associations = discovery.analyze_associations(test_file, [cls_sym, method_sym])

    # Method call should be statically associated
    method_assocs = [a for a in associations if a.target_symbol == "calc.Calculator.clear"]
    assert len(method_assocs) == 1
    assert method_assocs[0].classification == AssociationClassification.STATICALLY_ASSOCIATED
    assert method_assocs[0].evidence_kind == EvidenceKind.METHOD_CALL
    assert method_assocs[0].call_line_number == 5


def test_association_awaited_call(tmp_path: Path) -> None:
    test_file = tmp_path / "test_api.py"
    test_file.write_text(
        "from api import fetch_data\n"
        "\n"
        "async def test_fetch():\n"
        "    res = await fetch_data()\n"
        "    assert res is not None\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="api.fetch_data",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("api.py"),
        line_range=(1, 5),
        signature="async def fetch_data()",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.STATICALLY_ASSOCIATED
    assert associations[0].evidence_kind == EvidenceKind.AWAITED_CALL


def test_mock_disqualification_patch_decorator(tmp_path: Path) -> None:
    test_file = tmp_path / "test_notify.py"
    test_file.write_text(
        "from unittest.mock import patch\n"
        "from notify import send_alert\n"
        "\n"
        "@patch('notify.send_alert')\n"
        "def test_send_alert(mock_send):\n"
        "    send_alert('hello')\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="notify.send_alert",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("notify.py"),
        line_range=(1, 5),
        signature="def send_alert(msg)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assoc = associations[0]
    assert assoc.classification == AssociationClassification.UNSUPPORTED
    assert assoc.evidence_kind == EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED
    assert "mock/patch detected" in assoc.reason
    # extract_tested_symbols must exclude it!
    assert discovery.extract_tested_symbols(test_file) == []


def test_mock_disqualification_patch_object_decorator(tmp_path: Path) -> None:
    test_file = tmp_path / "test_srv.py"
    test_file.write_text(
        "from unittest.mock import patch\n"
        "from srv import Service\n"
        "\n"
        "@patch.object(Service, 'start')\n"
        "def test_start(mock_start):\n"
        "    s = Service()\n"
        "    s.start()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="srv.Service.start",
        symbol_type=SymbolType.METHOD,
        file_path=Path("srv.py"),
        line_range=(5, 8),
        signature="def start(self)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNSUPPORTED
    assert associations[0].evidence_kind == EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED
    assert discovery.extract_tested_symbols(test_file) == []


def test_mock_disqualification_with_patch(tmp_path: Path) -> None:
    test_file = tmp_path / "test_auth.py"
    test_file.write_text(
        "from unittest.mock import patch\n"
        "from auth import login\n"
        "\n"
        "def test_login():\n"
        "    with patch('auth.login'):\n"
        "        login()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="auth.login",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("auth.py"),
        line_range=(1, 5),
        signature="def login()",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNSUPPORTED
    assert associations[0].evidence_kind == EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED
    assert discovery.extract_tested_symbols(test_file) == []


def test_mock_disqualification_monkeypatch(tmp_path: Path) -> None:
    test_file = tmp_path / "test_log.py"
    test_file.write_text(
        "from logger import log_msg\n"
        "\n"
        "def test_log(monkeypatch):\n"
        "    monkeypatch.setattr('logger.log_msg', lambda m: None)\n"
        "    log_msg('hi')\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="logger.log_msg",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("logger.py"),
        line_range=(1, 5),
        signature="def log_msg(m)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNSUPPORTED
    assert associations[0].evidence_kind == EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED
    assert discovery.extract_tested_symbols(test_file) == []


def test_mock_disqualification_mocker_patch(tmp_path: Path) -> None:
    test_file = tmp_path / "test_ping.py"
    test_file.write_text(
        "from net import ping\n"
        "\n"
        "def test_ping(mocker):\n"
        "    mocker.patch('net.ping')\n"
        "    ping()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="net.ping",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("net.py"),
        line_range=(1, 5),
        signature="def ping()",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNSUPPORTED
    assert associations[0].evidence_kind == EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED
    assert discovery.extract_tested_symbols(test_file) == []


def test_ambiguous_external_fixture(tmp_path: Path) -> None:
    test_file = tmp_path / "test_db.py"
    test_file.write_text(
        "def test_query(connect_fixture):\n"
        "    pass\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="db.connect",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("db.py"),
        line_range=(1, 5),
        signature="def connect()",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNKNOWN
    assert associations[0].evidence_kind == EvidenceKind.EXTERNAL_FIXTURE
    assert discovery.extract_tested_symbols(test_file) == []


def test_ambiguous_dynamic_dispatch(tmp_path: Path) -> None:
    test_file = tmp_path / "test_exec.py"
    test_file.write_text(
        "import cmd_mod\n"
        "\n"
        "def test_exec():\n"
        "    getattr(cmd_mod, 'execute')('start')\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="cmd_mod.execute",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("cmd_mod.py"),
        line_range=(1, 5),
        signature="def execute(action)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNKNOWN
    assert associations[0].evidence_kind == EvidenceKind.DYNAMIC_DISPATCH
    assert discovery.extract_tested_symbols(test_file) == []


def test_ambiguous_complex_receiver(tmp_path: Path) -> None:
    test_file = tmp_path / "test_tree.py"
    test_file.write_text(
        "import tree_mod\n"
        "\n"
        "def test_prune():\n"
        "    tree_mod.get_factory().create_tree().prune()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="tree_mod.Tree.prune",
        symbol_type=SymbolType.METHOD,
        file_path=Path("tree_mod.py"),
        line_range=(10, 15),
        signature="def prune(self)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNKNOWN
    assert associations[0].evidence_kind == EvidenceKind.COMPLEX_RECEIVER
    assert discovery.extract_tested_symbols(test_file) == []


def test_evidence_triggered_emission_omits_unreferenced_symbols(tmp_path: Path) -> None:
    test_file = tmp_path / "test_math.py"
    test_file.write_text(
        "from math_ops import add\n"
        "\n"
        "def test_add():\n"
        "    assert add(1, 1) == 2\n",
        encoding="utf-8",
    )

    sym_add = SymbolContract(
        qualified_name="math_ops.add",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("math_ops.py"),
        line_range=(1, 3),
        signature="def add(a, b)",
    )
    sym_delete = SymbolContract(
        qualified_name="storage.delete_record",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("storage.py"),
        line_range=(10, 20),
        signature="def delete_record(id)",
    )

    discovery = TestDiscovery(target_symbols=[sym_add, sym_delete])
    associations = discovery.analyze_associations(test_file, [sym_add, sym_delete])

    # Exactly 1 record emitted for sym_add; 0 records emitted for unreferenced sym_delete
    assert len(associations) == 1
    assert associations[0].target_symbol == "math_ops.add"
    assert associations[0].classification == AssociationClassification.STATICALLY_ASSOCIATED


def test_zero_target_code_execution_in_test_discovery(tmp_path: Path) -> None:
    test_file = tmp_path / "test_side_effects.py"
    test_file.write_text(
        "import sys\n"
        "raise RuntimeError('Test file code executed on host during static analysis!')\n"
        "from safe_mod import safe_func\n"
        "def test_safe():\n"
        "    safe_func()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="safe_mod.safe_func",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("safe_mod.py"),
        line_range=(1, 3),
        signature="def safe_func()",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    # Pure ast.parse must NOT execute the top-level raise RuntimeError
    associations = discovery.analyze_associations(test_file, [sym])
    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.STATICALLY_ASSOCIATED


def test_mock_disqualification_mocker_patch_object(tmp_path: Path) -> None:
    test_file = tmp_path / "test_srv_mocker.py"
    test_file.write_text(
        "from srv import Service\n"
        "\n"
        "def test_start(mocker):\n"
        "    mocker.patch.object(Service, 'start')\n"
        "    s = Service()\n"
        "    s.start()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="srv.Service.start",
        symbol_type=SymbolType.METHOD,
        file_path=Path("srv.py"),
        line_range=(5, 8),
        signature="def start(self)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNSUPPORTED
    assert associations[0].evidence_kind == EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED
    assert discovery.extract_tested_symbols(test_file) == []


def test_mock_disqualification_aliased_patch_decorator_and_aliased_class(tmp_path: Path) -> None:
    test_file = tmp_path / "test_aliased_patch.py"
    test_file.write_text(
        "from unittest.mock import patch as my_patch\n"
        "from srv import Service as MyService\n"
        "\n"
        "@my_patch.object(MyService, 'start')\n"
        "def test_aliased_patch_target(mock_start):\n"
        "    s = MyService()\n"
        "    s.start()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="srv.Service.start",
        symbol_type=SymbolType.METHOD,
        file_path=Path("srv.py"),
        line_range=(5, 8),
        signature="def start(self)",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNSUPPORTED
    assert associations[0].evidence_kind == EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED
    assert discovery.extract_tested_symbols(test_file) == []


def test_ambiguous_unresolved_alias(tmp_path: Path) -> None:
    test_file = tmp_path / "test_alias.py"
    test_file.write_text(
        "def test_unresolved():\n"
        "    action = get_dynamic_callable()\n"
        "    action()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="core.action",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("core.py"),
        line_range=(1, 5),
        signature="def action()",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNKNOWN
    assert associations[0].evidence_kind == EvidenceKind.UNRESOLVED_ALIAS
    assert discovery.extract_tested_symbols(test_file) == []


def test_ambiguous_external_helper(tmp_path: Path) -> None:
    test_file = tmp_path / "test_helper.py"
    test_file.write_text(
        "from worker import execute_task\n"
        "\n"
        "def helper_run():\n"
        "    execute_task()\n"
        "\n"
        "def test_via_helper():\n"
        "    helper_run()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="worker.execute_task",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("worker.py"),
        line_range=(1, 5),
        signature="def execute_task()",
    )

    discovery = TestDiscovery(target_symbols=[sym])
    associations = discovery.analyze_associations(test_file, [sym])

    assert len(associations) == 1
    assert associations[0].classification == AssociationClassification.UNKNOWN
    assert associations[0].evidence_kind == EvidenceKind.EXTERNAL_HELPER
    assert discovery.extract_tested_symbols(test_file) == []


def test_mock_disqualification_same_symbol_distinct_modules(tmp_path: Path) -> None:
    """
    Verifies that mocking a symbol in one module (pkg.mod_a.execute) does NOT
    disqualify a symbol with the same simple name in a distinct module (pkg.mod_b.execute)
    (Corrective Task 2 & 4).
    """
    test_file = tmp_path / "test_mock_distinct.py"
    test_file.write_text(
        "from unittest.mock import patch\n"
        "from pkg.mod_a import execute\n"
        "\n"
        "@patch('pkg.mod_a.execute')\n"
        "def test_a(mock_exec):\n"
        "    execute()\n",
        encoding="utf-8",
    )

    sym_a = SymbolContract(
        qualified_name="pkg.mod_a.execute",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/pkg/mod_a.py"),
        line_range=(1, 5),
        signature="def execute()",
    )
    sym_b = SymbolContract(
        qualified_name="pkg.mod_b.execute",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/pkg/mod_b.py"),
        line_range=(1, 5),
        signature="def execute()",
    )

    discovery = TestDiscovery(target_symbols=[sym_b, sym_a])
    assocs = discovery.analyze_associations(test_file, [sym_b, sym_a])

    # sym_a is mocked and therefore UNSUPPORTED
    assocs_a = [a for a in assocs if a.target_symbol == "pkg.mod_a.execute"]
    assert len(assocs_a) == 1
    assert assocs_a[0].classification == AssociationClassification.UNSUPPORTED
    assert assocs_a[0].evidence_kind == EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED

    # sym_b was never mocked or called in this file, so it MUST NOT be emitted
    assocs_b = [a for a in assocs if a.target_symbol == "pkg.mod_b.execute"]
    assert len(assocs_b) == 0


def test_positive_call_same_symbol_distinct_modules(tmp_path: Path) -> None:
    """
    Verifies that direct import 'from foo import execute' strictly associates with
    'foo.execute' and does NOT match 'my_pkg_foo.execute' via suffix matching (Corrective Task 2 & 4).
    """
    test_file = tmp_path / "test_positive_distinct.py"
    test_file.write_text(
        "from foo import execute\n"
        "\n"
        "def test_exec():\n"
        "    execute()\n",
        encoding="utf-8",
    )

    sym_foo = SymbolContract(
        qualified_name="foo.execute",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/foo.py"),
        line_range=(1, 5),
        signature="def execute()",
    )
    sym_my_pkg_foo = SymbolContract(
        qualified_name="my_pkg_foo.execute",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/my_pkg_foo.py"),
        line_range=(1, 5),
        signature="def execute()",
    )

    discovery = TestDiscovery(target_symbols=[sym_my_pkg_foo, sym_foo])
    assocs = discovery.analyze_associations(test_file, [sym_my_pkg_foo, sym_foo])

    # foo.execute matches directly
    assocs_foo = [a for a in assocs if a.target_symbol == "foo.execute"]
    assert len(assocs_foo) == 1
    assert assocs_foo[0].classification == AssociationClassification.STATICALLY_ASSOCIATED
    assert assocs_foo[0].evidence_kind == EvidenceKind.DIRECT_CALL

    # my_pkg_foo.execute must NOT match via suffix substring
    assocs_my_pkg_foo = [a for a in assocs if a.target_symbol == "my_pkg_foo.execute"]
    assert len(assocs_my_pkg_foo) == 0


def test_module_qualified_chained_attribute_call(tmp_path: Path) -> None:
    """
    Verifies that chained module calls such as 'pkg.mod_a.execute()' are correctly
    resolved and associated (Corrective Task 2 & 4).
    """
    test_file = tmp_path / "test_chained.py"
    test_file.write_text(
        "import pkg.mod_a\n"
        "\n"
        "def test_call():\n"
        "    pkg.mod_a.execute()\n",
        encoding="utf-8",
    )

    sym_a = SymbolContract(
        qualified_name="pkg.mod_a.execute",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/pkg/mod_a.py"),
        line_range=(1, 5),
        signature="def execute()",
    )

    discovery = TestDiscovery(target_symbols=[sym_a])
    assocs = discovery.analyze_associations(test_file, [sym_a])

    assert len(assocs) == 1
    assert assocs[0].target_symbol == "pkg.mod_a.execute"
    assert assocs[0].classification == AssociationClassification.STATICALLY_ASSOCIATED
    assert assocs[0].evidence_kind == EvidenceKind.MODULE_QUALIFIED_CALL


def test_modules_match_directional_precision_prevents_shallower_symbol_match(tmp_path: Path) -> None:
    """
    Verifies that importing a deeper module 'from common.helpers import run'
    strictly associates with 'common.helpers.run' (exact) or 'src.common.helpers.run' (valid suffix),
    and NEVER falsely matches a shallower root symbol 'helpers.run'.
    """
    test_file = tmp_path / "test_common.py"
    test_file.write_text(
        "from common.helpers import run\n"
        "\n"
        "def test_run():\n"
        "    run()\n",
        encoding="utf-8",
    )

    sym_shallower = SymbolContract(
        qualified_name="helpers.run",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("helpers.py"),
        line_range=(1, 5),
        signature="def run()",
    )
    sym_exact = SymbolContract(
        qualified_name="common.helpers.run",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("common/helpers.py"),
        line_range=(1, 5),
        signature="def run()",
    )
    sym_prefixed = SymbolContract(
        qualified_name="src.common.helpers.run",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/common/helpers.py"),
        line_range=(1, 5),
        signature="def run()",
    )

    discovery = TestDiscovery()
    assocs = discovery.analyze_associations(test_file, [sym_shallower, sym_exact, sym_prefixed])

    matched_targets = {
        a.target_symbol
        for a in assocs
        if a.classification == AssociationClassification.STATICALLY_ASSOCIATED
    }

    # Exact module match must succeed
    assert "common.helpers.run" in matched_targets

    # Valid prefix/suffix match (e.g. repo src root) must succeed
    assert "src.common.helpers.run" in matched_targets

    # Shallower root symbol must NOT match (false-positive prevented)
    assert "helpers.run" not in matched_targets

