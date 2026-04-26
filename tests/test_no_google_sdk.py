"""CI invariant: the agent codebase must never import Google SDK clients directly.

All Google access goes through MCP servers. This test fails the build if any
forbidden import appears under src/pulse/.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Patterns that must NOT appear in src/pulse/ source files.
FORBIDDEN_IMPORTS = [
    "googleapiclient",
    "google.auth",
    "google.oauth2",
    "google_auth_oauthlib",
    "google.cloud",
]

SRC_ROOT = Path(__file__).parent.parent / "src" / "pulse"


def _collect_import_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def _python_files() -> list[Path]:
    return list(SRC_ROOT.rglob("*.py"))


@pytest.mark.parametrize("py_file", _python_files())
def test_no_google_sdk_import(py_file: Path) -> None:
    source = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        pytest.skip(f"Syntax error in {py_file} — checked separately")
        return

    imported = _collect_import_names(tree)
    violations = [
        (name, forbidden)
        for name in imported
        for forbidden in FORBIDDEN_IMPORTS
        if name.startswith(forbidden)
    ]
    assert not violations, (
        f"{py_file.relative_to(SRC_ROOT.parent.parent)} imports forbidden Google SDK module(s): "
        f"{[v[1] for v in violations]}. "
        "All Google access must go through MCP servers."
    )
