"""core/sandbox/tools_code.py — Sandboxed Code Intelligence Tools for OPTIMUS.

Provides:
- AST structure exploration and symbol search.
- Code explanation and structural complexity analysis.
- Safe test runner invocation within workspace boundaries.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import Any

from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolResult,
    _resolve_safe_path,
)
from core.sandbox.command_guard import execute_sandboxed_command


def code_ast_search_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Analyze Python code AST to inspect classes, functions, imports, and detect syntax errors."""
    raw_path = str(params.get("path") or "")
    symbol_filter = str(params.get("symbol") or "").lower().strip()
    allowed_roots = (context or {}).get("allowed_roots")

    try:
        target_path = _resolve_safe_path(raw_path, allowed_roots)
        if not target_path.is_file():
            return ToolResult(status="failed", error=f"File not found: '{target_path}'")

        code_text = target_path.read_text(encoding="utf-8", errors="replace")
        digest = hashlib.sha256(code_text.encode("utf-8")).hexdigest()

        try:
            tree = ast.parse(code_text, filename=target_path.name)
        except SyntaxError as syn_err:
            return ToolResult(
                status="failed",
                error=f"SYNTAX_ERROR at line {syn_err.lineno}, col {syn_err.offset}: {syn_err.msg}",
                evidence={"path": str(target_path), "line": syn_err.lineno, "error": syn_err.msg},
            )

        classes = []
        functions = []
        imports = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                doc = ast.get_docstring(node) or ""
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "methods": methods,
                    "docstring": doc[:120],
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                doc = ast.get_docstring(node) or ""
                functions.append({
                    "name": node.name,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "line": node.lineno,
                    "arguments": args,
                    "docstring": doc[:120],
                })
            elif isinstance(node, ast.Import):
                for n in node.names:
                    imports.append(n.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for n in node.names:
                    imports.append(f"{mod}.{n.name}")

        # Filter by symbol if requested
        if symbol_filter:
            classes = [c for c in classes if symbol_filter in c["name"].lower()]
            functions = [f for f in functions if symbol_filter in f["name"].lower()]

        lines = [
            f"=== Code AST Analysis: {target_path.name} ===",
            f"Imports: {', '.join(imports[:15]) or 'None'}",
            f"Classes ({len(classes)}):",
        ]
        for c in classes:
            lines.append(f"  - class {c['name']} (L{c['line']}): methods={c['methods']}")
        lines.append(f"Functions ({len(functions)}):")
        for f in functions:
            prefix = "async def" if f["is_async"] else "def"
            lines.append(f"  - {prefix} {f['name']}({', '.join(f['arguments'])}) (L{f['line']})")

        output_summary = "\n".join(lines)
        return ToolResult(
            status="done",
            output=output_summary,
            evidence={
                "path": str(target_path),
                "sha256": digest,
                "class_count": len(classes),
                "function_count": len(functions),
                "import_count": len(imports),
            },
        )
    except Exception as ex:
        return ToolResult(status="failed", error=f"AST search error: {ex}")


def code_explain_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Produce structural explanation and complexity breakdown of a code file."""
    raw_path = str(params.get("path") or "")
    allowed_roots = (context or {}).get("allowed_roots")

    try:
        target_path = _resolve_safe_path(raw_path, allowed_roots)
        if not target_path.is_file():
            return ToolResult(status="failed", error=f"File not found: '{target_path}'")

        lines = target_path.read_text(encoding="utf-8", errors="replace").splitlines()
        loc = len(lines)
        blank_lines = sum(1 for line in lines if not line.strip())
        comment_lines = sum(1 for line in lines if line.strip().startswith("#"))
        code_lines = loc - blank_lines - comment_lines

        # Simple cyclomatic branch counter
        branch_keywords = ("if ", "elif ", "for ", "while ", "except ", "try:", "with ")
        branches = sum(1 for line in lines if any(line.strip().startswith(kw) for kw in branch_keywords))

        summary = (
            f"### Structural Code Explanation: `{target_path.name}`\n"
            f"- **Total Lines:** {loc} (Code: {code_lines}, Comments: {comment_lines}, Blank: {blank_lines})\n"
            f"- **Estimated Branching/Complexity Points:** {branches}\n"
            f"- **Language/Suffix:** {target_path.suffix}\n"
            f"- **Path:** `{target_path}`"
        )
        return ToolResult(
            status="done",
            output=summary,
            evidence={"path": str(target_path), "loc": loc, "code_lines": code_lines, "branches": branches},
        )
    except Exception as ex:
        return ToolResult(status="failed", error=f"Code explain error: {ex}")


def code_test_runner_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Run pytest suite safely within the workspace boundary."""
    test_target = str(params.get("test_path") or "tests")
    allowed_roots = (context or {}).get("allowed_roots")
    cwd = Path(allowed_roots[0]) if allowed_roots else Path.cwd()

    try:
        target_path = _resolve_safe_path(test_target, allowed_roots)
    except Exception as ex:
        return ToolResult(status="failed", error=f"Invalid test path: {ex}")

    py_exe = sys.executable
    cmd = f'"{py_exe}" -m pytest -q "{target_path}"'
    return execute_sandboxed_command(cmd, cwd=cwd, timeout=30.0, allowed_roots=allowed_roots)


def get_code_tools() -> list[ToolDefinition]:
    """Return tool definitions for the Code Intelligence Agent."""
    return [
        ToolDefinition(
            name="code_ast_search",
            description="Explore Python code structure: classes, functions, imports, docstrings, and detect syntax errors.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Relative or absolute path to Python source file"},
                    "symbol": {"type": "STRING", "description": "Optional symbol or function name substring filter"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=code_ast_search_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="code_explain",
            description="Analyze code structure, metrics, branching complexity, and line distribution.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Path to source code file"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=code_explain_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="code_test_runner",
            description="Execute automated tests (pytest) within the workspace boundary.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "test_path": {"type": "STRING", "description": "Relative path to test file or directory (default: 'tests')"},
                },
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=code_test_runner_handler,
            is_mutating=False,
        ),
    ]
