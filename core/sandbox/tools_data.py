"""core/sandbox/tools_data.py — Sandboxed Data Intelligence Tools for OPTIMUS.

Provides:
- Tabular CSV/JSON ingestion, delimiter detection, and schema inference.
- Data filtering, column selection, and sorting transformations.
- Statistical analysis (mean, median, stddev, min, max, null counts).
- Structured tabular report generation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from typing import Any

from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolResult,
    _resolve_safe_path,
)


def data_read_tabular_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Read and summarize CSV, TSV, or JSON tabular data files."""
    raw_path = str(params.get("path") or "")
    max_preview = int(params.get("max_rows") or 10)
    allowed_roots = (context or {}).get("allowed_roots")

    try:
        target_path = _resolve_safe_path(raw_path, allowed_roots)
        if not target_path.is_file():
            return ToolResult(status="failed", error=f"File not found: '{target_path}'")

        text = target_path.read_text(encoding="utf-8", errors="replace")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        if target_path.suffix.lower() == ".json":
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    headers = list(data[0].keys()) if data and isinstance(data[0], dict) else []
                    rows = [[str(item.get(h, "")) for h in headers] for item in data if isinstance(item, dict)]
                elif isinstance(data, dict):
                    headers = list(data.keys())
                    rows = [[str(data[h]) for h in headers]]
                else:
                    return ToolResult(status="failed", error="JSON does not contain tabular structure.")
            except Exception as e:
                return ToolResult(status="failed", error=f"JSON parse error: {e}")
        else:
            # CSV / TSV handling
            dialect = csv.Sniffer().sniff(text[:2048]) if len(text) > 20 else csv.excel
            reader = csv.reader(io.StringIO(text), dialect)
            all_rows = list(reader)
            if not all_rows:
                return ToolResult(status="failed", error="Dataset is empty.")
            headers = [h.strip() for h in all_rows[0]]
            rows = all_rows[1:]

        preview_rows = rows[:max_preview]
        lines = [
            f"### Tabular Dataset: `{target_path.name}`",
            f"- **Total Rows:** {len(rows)} | **Columns ({len(headers)}):** {', '.join(headers)}",
            f"\n| {' | '.join(headers)} |",
            f"| {' | '.join(['---'] * len(headers))} |",
        ]
        for r in preview_rows:
            padded = (r + [""] * len(headers))[:len(headers)]
            lines.append(f"| {' | '.join(padded)} |")

        return ToolResult(
            status="done",
            output="\n".join(lines),
            evidence={"path": str(target_path), "row_count": len(rows), "columns": headers, "sha256": digest},
        )
    except Exception as ex:
        return ToolResult(status="failed", error=f"Tabular read error: {ex}")


def data_transform_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Filter, select columns, and transform tabular dataset, writing output to destination."""
    raw_path = str(params.get("path") or "")
    out_path = str(params.get("output_path") or "")
    select_cols = params.get("select_columns")
    filter_col = params.get("filter_column")
    filter_op = str(params.get("filter_op") or "eq").lower()
    filter_val = params.get("filter_val")
    allowed_roots = (context or {}).get("allowed_roots")

    if not out_path:
        return ToolResult(status="failed", error="Missing output_path parameter.")

    try:
        src = _resolve_safe_path(raw_path, allowed_roots)
        dst = _resolve_safe_path(out_path, allowed_roots)
        if not src.is_file():
            return ToolResult(status="failed", error=f"Source file not found: '{src}'")

        text = src.read_text(encoding="utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        all_rows = list(reader)
        if not all_rows:
            return ToolResult(status="failed", error="Dataset is empty.")

        src_headers = [h.strip() for h in all_rows[0]]
        src_rows = all_rows[1:]

        # Filter rows
        filtered_rows = src_rows
        if filter_col and filter_col in src_headers and filter_val is not None:
            c_idx = src_headers.index(filter_col)
            str_val = str(filter_val).strip()

            def _match(r: list[str]) -> bool:
                cell = r[c_idx].strip() if c_idx < len(r) else ""
                if filter_op == "eq":
                    return cell == str_val
                elif filter_op == "ne":
                    return cell != str_val
                try:
                    num_cell = float(cell)
                    num_target = float(str_val)
                    if filter_op == "gt":
                        return num_cell > num_target
                    elif filter_op == "lt":
                        return num_cell < num_target
                except ValueError:
                    pass
                return cell == str_val

            filtered_rows = [r for r in src_rows if _match(r)]

        # Select columns
        if select_cols and isinstance(select_cols, list):
            target_indices = [src_headers.index(c) for c in select_cols if c in src_headers]
            final_headers = [src_headers[i] for i in target_indices]
            final_rows = [[r[i] if i < len(r) else "" for i in target_indices] for r in filtered_rows]
        else:
            final_headers = src_headers
            final_rows = filtered_rows

        # Write output
        dst.parent.mkdir(parents=True, exist_ok=True)
        out_buf = io.StringIO()
        writer = csv.writer(out_buf)
        writer.writerow(final_headers)
        writer.writerows(final_rows)
        out_bytes = out_buf.getvalue().encode("utf-8")
        dst.write_bytes(out_bytes)
        digest = hashlib.sha256(out_bytes).hexdigest()

        return ToolResult(
            status="done",
            output=f"Transformed {len(final_rows)} rows to '{dst.name}' ({len(out_bytes)} bytes).",
            evidence={
                "path": str(dst),
                "bytes": len(out_bytes),
                "sha256": digest,
                "row_count": len(final_rows),
                "columns": final_headers,
            },
        )
    except Exception as ex:
        return ToolResult(status="failed", error=f"Data transform error: {ex}")


def data_compute_statistics_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Calculate statistical summaries for numeric columns in a tabular dataset."""
    raw_path = str(params.get("path") or "")
    allowed_roots = (context or {}).get("allowed_roots")

    try:
        src = _resolve_safe_path(raw_path, allowed_roots)
        if not src.is_file():
            return ToolResult(status="failed", error=f"File not found: '{src}'")

        text = src.read_text(encoding="utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        all_rows = list(reader)
        if not all_rows:
            return ToolResult(status="failed", error="Dataset is empty.")

        headers = [h.strip() for h in all_rows[0]]
        rows = all_rows[1:]

        col_stats: dict[str, Any] = {}
        lines = [f"### Statistical Analysis: `{src.name}` ({len(rows)} rows)"]

        for idx, col in enumerate(headers):
            values = []
            null_count = 0
            for r in rows:
                cell = r[idx].strip() if idx < len(r) else ""
                if not cell:
                    null_count += 1
                    continue
                try:
                    values.append(float(cell))
                except ValueError:
                    continue

            # Only analyze if column has numeric values
            if values:
                v_count = len(values)
                v_mean = round(sum(values) / v_count, 3)
                v_min = min(values)
                v_max = max(values)
                sorted_v = sorted(values)
                v_med = sorted_v[v_count // 2]
                variance = sum((x - v_mean) ** 2 for x in values) / v_count
                v_std = round(math.sqrt(variance), 3)

                stats = {
                    "count": v_count,
                    "nulls": null_count,
                    "min": v_min,
                    "max": v_max,
                    "mean": v_mean,
                    "median": v_med,
                    "stddev": v_std,
                }
                col_stats[col] = stats
                lines.append(f"- **{col}**: mean={v_mean}, median={v_med}, range=[{v_min}, {v_max}], stddev={v_std}, nulls={null_count}")

        output = "\n".join(lines) if col_stats else "No numerical columns detected."
        return ToolResult(status="done", output=output, evidence={"statistics": col_stats, "path": str(src)})
    except Exception as ex:
        return ToolResult(status="failed", error=f"Statistical calculation error: {ex}")


def get_data_tools() -> list[ToolDefinition]:
    """Return tool definitions for the Data Intelligence Agent."""
    return [
        ToolDefinition(
            name="data_read_tabular",
            description="Read and inspect tabular datasets (CSV, TSV, JSON).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Path to dataset file"},
                    "max_rows": {"type": "INTEGER", "description": "Preview row count (default 10)"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=data_read_tabular_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="data_transform",
            description="Filter, subset columns, and write transformed dataset.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Source dataset path"},
                    "output_path": {"type": "STRING", "description": "Destination file path"},
                    "select_columns": {"type": "ARRAY", "description": "List of columns to keep"},
                    "filter_column": {"type": "STRING", "description": "Column to filter on"},
                    "filter_op": {"type": "STRING", "description": "Filter operator: eq, ne, gt, lt"},
                    "filter_val": {"type": "STRING", "description": "Filter value to match"},
                },
                "required": ["path", "output_path"],
            },
            capability_tier=CapabilityTier.WORKSPACE_WRITE,
            handler=data_transform_handler,
            is_mutating=True,
        ),
        ToolDefinition(
            name="data_compute_statistics",
            description="Compute summary statistics (mean, median, stddev, range, nulls) for numeric columns.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Path to tabular file"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=data_compute_statistics_handler,
            is_mutating=False,
        ),
    ]
