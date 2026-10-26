"""Guards the import boundaries between the core and the presentation layers."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

FORBIDDEN_IMPORTS: dict[str, set[str]] = {
    "typerek_core": {"textual", "PySide6", "typerek_tui", "typerek_gui"},
    "typerek_tui": {"PySide6", "typerek_gui"},
    "typerek_gui": {"textual", "typerek_tui"},
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("package", sorted(FORBIDDEN_IMPORTS))
def test_package_respects_import_boundaries(package: str) -> None:
    forbidden = FORBIDDEN_IMPORTS[package]
    files = sorted((SRC / package).rglob("*.py"))
    assert files, f"No files found in package {package}"

    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in files
        for module in imported_modules(path)
        if module.split(".")[0] in forbidden
    ]
    assert not violations, "Forbidden imports:\n" + "\n".join(violations)
