"""No libvirt call may run on the UI thread.

libvirt-python is synchronous. Locally a call is under a millisecond, so the
mistake is invisible; over qemu+ssh it's a network round trip and the window
stops repainting. So every service call goes through run_task, as a lambda or
as a worker function passed to it.

This walks the UI packages and fails on any svc_* call outside one.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
UI_DIRS = ("pages", "dialogs", "widgets")
UI_FILES = ("main_window.py", "wizard.py", "tasks.py", "topology.py", "palette.py")


# Faceplate fields register an applier rather than calling run_task
# themselves: _save_fields is what runs them, inside a worker. The argument
# that holds the applier, per registrar.
APPLIER_ARG = {"_panel_field": 3, "_panel_watch": 2}


def _worker_names(tree: ast.Module) -> set[str]:
    """Callables this module hands to run_task, directly or through a field."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if called == "run_task":
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Name):
                    names.add(arg.id)
                elif isinstance(arg, ast.Lambda):
                    names.add("<lambda>")
        elif called in APPLIER_ARG:
            index = APPLIER_ARG[called]
            applier = node.args[index] if len(node.args) > index else None
            if isinstance(applier, ast.Name):
                names.add(applier.id)
            for kw in node.keywords:
                if kw.arg == "apply" and isinstance(kw.value, ast.Name):
                    names.add(kw.value.id)
    return names


def _offenders(path: Path) -> list[tuple[int, str, str]]:
    tree = ast.parse(path.read_text())
    workers = _worker_names(tree)
    found: list[tuple[int, str, str]] = []

    class Visit(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Lambda(self, node: ast.Lambda) -> None:
            self.stack.append("<lambda>")
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            if name.startswith("svc_"):
                enclosing = self.stack[-1] if self.stack else "<module>"
                if enclosing != "<lambda>" and enclosing not in workers:
                    found.append((node.lineno, name, " > ".join(self.stack)))
            self.generic_visit(node)

    Visit().visit(tree)
    return found


def _ui_modules() -> list[Path]:
    paths = [
        p for directory in UI_DIRS
        for p in (PROJECT / "vmmanager" / directory).rglob("*.py")
    ]
    paths += [PROJECT / "vmmanager" / name for name in UI_FILES]
    return [p for p in paths if p.exists()]


def test_the_detector_catches_a_planted_violation(tmp_path):
    """A test that can't fail isn't a test."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "class P:\n"
        "    def refresh(self):\n"
        "        pools = svc_list_pools()\n"
        "        run_task(lambda: svc_delete_pool(1), done=self.ok)\n"
    )
    assert [(line, name) for line, name, _ in _offenders(sample)] == [
        (3, "svc_list_pools")
    ]


def test_a_field_applier_counts_as_a_worker(tmp_path):
    """_save_fields runs these inside run_task, so they are not offenders -
    but only the argument that actually holds the applier."""
    sample = tmp_path / "field.py"
    sample.write_text(
        "class P:\n"
        "    def face(self):\n"
        "        def save(v):\n"
        "            return svc_set_memory(self.uuid, v)\n"
        "        def read():\n"
        "            return svc_get_memory()\n"
        "        self._panel_field('mem', box, read, save)\n"
    )
    assert [name for _line, name, _w in _offenders(sample)] == ["svc_get_memory"]


def test_no_service_call_runs_on_the_ui_thread():
    problems = [
        f"{path.relative_to(PROJECT)}:{line} {name} (in {where})"
        for path in _ui_modules()
        for line, name, where in _offenders(path)
    ]
    assert problems == [], "wrap these in run_task:\n" + "\n".join(problems)
