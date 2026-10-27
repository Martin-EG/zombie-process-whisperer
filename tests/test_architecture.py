"""Static checks over zombie/ with the ast module (invariants S2 and S3)."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "zombie"
SCRIPTS = ROOT / "scripts"


def _sources(*folders: Path) -> list[tuple[str, ast.Module]]:
    out = []
    for folder in folders:
        for path in sorted(folder.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            out.append((rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel)))
    return out


def imports_subprocess(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "subprocess" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "subprocess":
                return True
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            first = node.args[0]
            if (
                name in {"__import__", "import_module"}
                and isinstance(first, ast.Constant)
                and first.value == "subprocess"
            ):
                return True
    return False


def references_run_mutation(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "run_mutation":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "run_mutation":
            return True
        if isinstance(node, ast.ImportFrom) and any(a.name == "run_mutation" for a in node.names):
            return True
    return False


def shell_not_false_calls(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False
                ):
                    lines.append(node.lineno)
    return lines


def test_detectors_catch_bad_code() -> None:
    assert imports_subprocess(ast.parse("import subprocess"))
    assert imports_subprocess(ast.parse("from subprocess import run"))
    assert imports_subprocess(ast.parse("import subprocess as sp"))
    assert imports_subprocess(ast.parse("x = __import__('subprocess')"))
    assert imports_subprocess(ast.parse("importlib.import_module('subprocess')"))
    assert not imports_subprocess(ast.parse("import os"))
    assert references_run_mutation(ast.parse("proc.run_mutation(['x'])"))
    assert references_run_mutation(ast.parse("from zombie.proc import run_mutation"))
    assert references_run_mutation(ast.parse("f = run_mutation"))
    assert shell_not_false_calls(ast.parse("run(a, shell=True)")) == [1]
    assert shell_not_false_calls(ast.parse("run(a, shell=flag)")) == [1]
    assert shell_not_false_calls(ast.parse("run(a, shell=False)")) == []


def test_s3_only_proc_imports_subprocess() -> None:
    offenders = [rel for rel, tree in _sources(PACKAGE, SCRIPTS) if imports_subprocess(tree)]
    assert offenders == ["zombie/proc.py"]


def test_s2_only_bury_calls_run_mutation() -> None:
    allowed = {"zombie/proc.py", "zombie/bury.py"}  # proc.py defines it
    offenders = [
        rel
        for rel, tree in _sources(PACKAGE, SCRIPTS)
        if rel not in allowed and references_run_mutation(tree)
    ]
    assert offenders == []
    bury = dict(_sources(PACKAGE))["zombie/bury.py"]
    assert references_run_mutation(bury)  # the real caller, not an empty stub


def test_s3_no_shell_true() -> None:
    offenders = {
        rel: lines
        for rel, tree in _sources(PACKAGE, SCRIPTS)
        if (lines := shell_not_false_calls(tree))
    }
    assert offenders == {}
