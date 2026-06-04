"""AC-3 / AC-E: Scaffold and README present; PoC convention documented.

Asserts that the tests/security/ scaffold exists with the required files
and that the README documents the xfail-strict-until-hardened convention.

HL-E (security-redteam-campaign-RUN): broadens the bare-hard-fail detector to flag:
  - pytest.fail(...) with ANY args/kwargs (not just zero-arg)
  - raise AssertionError(...) outside xfail/except/raises
  - assert <falsy-constant> (assert 0, assert None) outside xfail/except/raises
  while STILL permitting all of these inside:
  - @pytest.mark.xfail-decorated functions
  - except blocks (ExceptHandler scope)
  - pytest.raises() context managers (With scope)
"""
from __future__ import annotations

import ast
import pathlib


_SECURITY_DIR = pathlib.Path(__file__).parent


def test_scaffold_and_readme_present() -> None:
    """AC-3: conftest.py, README.md, and __init__.py exist in tests/security/."""
    assert (_SECURITY_DIR / "conftest.py").exists(), "conftest.py missing"
    assert (_SECURITY_DIR / "README.md").exists(), "README.md missing"
    assert (_SECURITY_DIR / "__init__.py").exists(), "__init__.py missing"


def test_readme_documents_convention() -> None:
    """AC-3: README states the xfail-strict-until-hardened convention."""
    readme = (_SECURITY_DIR / "README.md").read_text()
    # The README must mention xfail(strict=True) and the xfail->pass lifecycle
    assert "xfail" in readme, "README must mention xfail convention"
    assert "strict" in readme, "README must mention strict=True"
    assert "harden" in readme.lower(), "README must describe hardening step"


def _collect_xfail_function_names(tree: ast.AST) -> set[str]:
    """Return names of functions decorated with pytest.mark.xfail (any form)."""
    xfail_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            deco_str = ast.dump(deco)
            # Match @pytest.mark.xfail in any variant (xfail / xfail(...))
            if "xfail" in deco_str:
                xfail_names.add(node.name)
    return xfail_names


class _BareHardFailDetector(ast.NodeVisitor):
    """AST visitor that detects bare hard failures outside allowed scopes.

    HL-E broadened checks (flags OUTSIDE xfail/except/pytest.raises):
      1. pytest.fail(...) with ANY args or kwargs (old: only zero-arg)
      2. raise AssertionError(...)
      3. assert <falsy-constant> (assert False, assert 0, assert None, assert "")

    Allowed scopes (these are NOT violations):
      - @pytest.mark.xfail-decorated functions
      - except/ExceptHandler blocks
      - pytest.raises() With blocks
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []
        self._xfail_functions: set[str] = set()
        self._current_function: str | None = None
        self._in_xfail_function: bool = False
        self._in_except: int = 0         # nesting counter
        self._in_pytest_raises: int = 0  # nesting counter

    def _is_allowed(self) -> bool:
        """True when inside an exempted scope."""
        return self._in_xfail_function or self._in_except > 0 or self._in_pytest_raises > 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Track current function and whether it is xfail-decorated."""
        old_func = self._current_function
        old_xfail = self._in_xfail_function

        self._current_function = node.name
        self._in_xfail_function = any(
            "xfail" in ast.dump(deco) for deco in node.decorator_list
        )
        self.generic_visit(node)

        self._current_function = old_func
        self._in_xfail_function = old_xfail

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Track except scope."""
        self._in_except += 1
        self.generic_visit(node)
        self._in_except -= 1

    def visit_With(self, node: ast.With) -> None:
        """Track pytest.raises() scope."""
        is_raises = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "raises"
            for item in node.items
        )
        if is_raises:
            self._in_pytest_raises += 1
        self.generic_visit(node)
        if is_raises:
            self._in_pytest_raises -= 1

    def visit_Expr(self, node: ast.Expr) -> None:
        """Detect pytest.fail(...) call as a statement."""
        if isinstance(node.value, ast.Call):
            call = node.value
            # Detect pytest.fail(...)
            is_pytest_fail = (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "pytest"
                and call.func.attr == "fail"
            )
            if is_pytest_fail and not self._is_allowed():
                # Flag whether it has args (HL-E: any pytest.fail call outside allowed scope)
                self.violations.append(
                    f"{self.filename}:{node.lineno}: bare pytest.fail(...) "
                    f"in non-xfail function '{self._current_function}'"
                )
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        """Detect raise AssertionError(...) outside allowed scope."""
        if (
            node.exc is not None
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "AssertionError"
            and not self._is_allowed()
        ):
            self.violations.append(
                f"{self.filename}:{node.lineno}: bare raise AssertionError(...) "
                f"in non-xfail function '{self._current_function}'"
            )
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        """Detect assert <falsy-constant> outside allowed scope.

        Falsy constants: False, 0, None, "" (empty string).
        assert True / assert <expression> are fine.
        """
        if (
            isinstance(node.test, ast.Constant)
            and not node.test.value  # Falsy: False, 0, None, ""
            and not self._is_allowed()
        ):
            self.violations.append(
                f"{self.filename}:{node.lineno}: unconditional 'assert {node.test.value!r}' "
                f"(falsy constant) in non-xfail function '{self._current_function}'"
            )
        self.generic_visit(node)


def test_no_bare_hard_fail_in_scaffold() -> None:
    """AC-3 / AC-E: No committed test has a bare hard-fail outside an xfail/except/raises.

    Convention (ao-meta-I): not-yet-hardened PoCs MUST use @pytest.mark.xfail(strict=True)
    and contain their hard-fail inside the xfail-decorated function.

    HL-E (security-redteam-campaign-RUN): broadened to also flag:
      - pytest.fail(...) WITH args (old version only flagged zero-arg calls)
      - raise AssertionError(...)
      - assert <falsy-constant> (assert False, assert 0, assert None)
    All still permitted inside @xfail functions, except blocks, and pytest.raises().

    The detector is self-tested by test_bare_fail_detector_catches_violations and
    test_bare_fail_detector_respects_allowed_scopes below.
    """
    test_files = list(_SECURITY_DIR.glob("test_*.py"))
    assert test_files, "No test_*.py files found in tests/security/"

    all_violations: list[str] = []

    for tf in test_files:
        source = tf.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # let pytest report syntax errors

        detector = _BareHardFailDetector(tf.name)
        detector.visit(tree)
        all_violations.extend(detector.violations)

    assert not all_violations, (
        "Bare unconditional hard-fails found outside xfail/except/pytest.raises "
        "(ao-meta-I AC-3 / HL-E: use @pytest.mark.xfail(strict=True) instead):\n"
        + "\n".join(all_violations)
    )


# ---------------------------------------------------------------------------
# Self-tests for the HL-E detector (AC-E: the linter must catch violations)
# ---------------------------------------------------------------------------

def test_bare_fail_detector_catches_violations() -> None:
    """AC-E: The broadened detector catches all three violation types."""
    import tempfile

    violation_code = '''\
import pytest

def test_bare_pytest_fail_with_args():
    pytest.fail("This should be flagged")  # violation: pytest.fail with args

def test_bare_raise_assertion():
    raise AssertionError("This should be flagged")  # violation: raise AssertionError

def test_bare_assert_false():
    assert False  # violation: assert False (falsy constant)

def test_bare_assert_zero():
    assert 0  # violation: assert 0 (falsy constant)

def test_bare_assert_none():
    assert None  # violation: assert None (falsy constant)
'''

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(violation_code)
        tmp = pathlib.Path(f.name)

    try:
        tree = ast.parse(violation_code)
        detector = _BareHardFailDetector(tmp.name)
        detector.visit(tree)
        violations = detector.violations

        assert len(violations) == 5, (
            f"Expected 5 violations (pytest.fail, raise AssertionError, assert False, "
            f"assert 0, assert None); got {len(violations)}: {violations}"
        )
    finally:
        tmp.unlink(missing_ok=True)


def test_bare_fail_detector_respects_allowed_scopes() -> None:
    """AC-E: The detector does NOT flag violations inside allowed scopes."""
    import tempfile

    safe_code = '''\
import pytest

@pytest.mark.xfail(strict=True, reason="known exploit")
def test_xfail_decorated():
    pytest.fail("inside xfail -- OK")       # allowed
    raise AssertionError("inside xfail")    # allowed
    assert False                            # allowed

def test_inside_except_block():
    try:
        risky = 1 / 0
    except ZeroDivisionError:
        pytest.fail("inside except -- OK")  # allowed
        raise AssertionError("OK too")      # allowed
        assert False                        # allowed

def test_inside_pytest_raises():
    with pytest.raises(ValueError):
        pytest.fail("inside raises -- OK")  # allowed

def test_assert_true_not_flagged():
    assert True                             # not falsy -- OK

def test_assert_expression_not_flagged():
    x = 1
    assert x == 1                          # expression, not constant -- OK
'''

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(safe_code)
        tmp = pathlib.Path(f.name)

    try:
        tree = ast.parse(safe_code)
        detector = _BareHardFailDetector(tmp.name)
        detector.visit(tree)
        violations = detector.violations

        assert len(violations) == 0, (
            f"Expected 0 violations in safe code; got {len(violations)}: {violations}"
        )
    finally:
        tmp.unlink(missing_ok=True)
