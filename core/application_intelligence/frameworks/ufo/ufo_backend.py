"""core/application_intelligence/frameworks/ufo/ufo_backend.py — Microsoft UFO² Desktop Backend.

Exposes Microsoft UFO² capabilities (WinCOM document automation, AppAgent action schemas)
through a narrow, typed BaseExecutionProvider interface (reconciles Section 2 & HIGH-1).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from core.application_intelligence.frameworks.base import (
    BaseExecutionProvider,
    ExecutionRequest,
    ExecutionResponse,
    ProviderHealthStatus,
)
from core.application_intelligence.frameworks.ufo.app_agent_schemas import UFOAppAgentSchemas
from core.application_intelligence.frameworks.ufo.wincom_bridge import WinCOMBridge

logger = logging.getLogger("optimus.application_intelligence.frameworks.ufo.backend")


class UFODesktopBackend(BaseExecutionProvider):
    """Microsoft UFO²-inspired desktop automation provider (WinCOM & AppAgent schemas)."""

    name = "ufo_desktop"
    requires_desktop_lock = False  # Background COM calls do not steal window focus (MED-1)

    def __init__(self) -> None:
        super().__init__()
        self._last_opened_docs: list[str] = []

    def health_probe(self) -> bool:
        """Non-mutating check verifying WinCOM/pywintypes are operational (MED-3)."""
        return WinCOMBridge.is_available()

    def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        """Execute a single atomic desktop or WinCOM action without autonomous loops (HIGH-1)."""
        sec_err = self.validate_security(request)
        if sec_err:
            return ExecutionResponse(
                action_id=request.action_id,
                status="failed",
                error_message=sec_err,
                facts=[f"security_check_failed: {sec_err}"],
            )

        op = request.operation.lower()
        start = time.perf_counter()

        # 1. Word Document Generation & PDF Export
        if op in ("create_word_document", "word_export", "com_word_document"):
            paragraphs = request.parameters.get("paragraphs", ["Document created by OPTIMUS UFO Backend."])
            out_path = request.parameters.get("file_path") or request.parameters.get("output_path", "output/document.docx")
            export_pdf = request.parameters.get("export_pdf", True)

            res = WinCOMBridge.word_create_and_export(
                paragraphs=paragraphs,
                output_path=out_path,
                export_pdf=export_pdf,
                timeout=request.timeout,
            )
            dur = (time.perf_counter() - start) * 1000.0
            if res.get("docx_path"):
                self._last_opened_docs.append(res["docx_path"])

            return ExecutionResponse(
                action_id=request.action_id,
                status="done" if res.get("success") else "failed",
                facts=[f"wincom_word_created: path='{res.get('docx_path')}', pdf='{res.get('pdf_path')}'"],
                evidence_data=res,
                duration_ms=dur,
            )

        # 2. Excel Spreadsheet Generation & CSV Export
        elif op in ("create_excel_sheet", "create_excel_spreadsheet", "excel_export", "com_excel_sheet"):
            rows = request.parameters.get("rows") or request.parameters.get("sheet_data", [["Header1", "Header2"], ["Value1", "Value2"]])
            out_path = request.parameters.get("file_path") or request.parameters.get("output_path", "output/data.xlsx")
            export_csv = request.parameters.get("export_csv", True)

            res = WinCOMBridge.excel_write_and_export(
                rows=rows,
                output_path=out_path,
                export_csv=export_csv,
                timeout=request.timeout,
            )
            dur = (time.perf_counter() - start) * 1000.0
            if res.get("xlsx_path"):
                self._last_opened_docs.append(res["xlsx_path"])

            return ExecutionResponse(
                action_id=request.action_id,
                status="done" if res.get("success") else "failed",
                facts=[f"wincom_excel_created: path='{res.get('xlsx_path')}', csv='{res.get('csv_path')}'"],
                evidence_data=res,
                duration_ms=dur,
            )

        # 3. Windows Settings Action Schema
        elif op in ("open_settings", "settings_navigate", "ufo_settings", "navigate_settings"):
            category = request.parameters.get("setting_name") or request.parameters.get("category", "apps")
            res = UFOAppAgentSchemas.launch_settings_category(category)
            dur = (time.perf_counter() - start) * 1000.0
            return ExecutionResponse(
                action_id=request.action_id,
                status="done" if res.get("success") else "failed",
                facts=[f"ufo_settings_opened: category='{category}'"],
                evidence_data=res,
                duration_ms=dur,
            )

        # 4. File Explorer Action Schema
        elif op in ("open_explorer", "explorer_select", "ufo_explorer"):
            folder = request.parameters.get("folder", request.parameters.get("path", "."))
            file_to_sel = request.parameters.get("file_to_select")
            res = UFOAppAgentSchemas.explorer_navigate_and_select(folder, file_to_sel)
            dur = (time.perf_counter() - start) * 1000.0
            return ExecutionResponse(
                action_id=request.action_id,
                status="done" if res.get("success") else "failed",
                facts=[f"ufo_explorer_navigated: path='{folder}'"],
                evidence_data=res,
                duration_ms=dur,
            )

        dur = (time.perf_counter() - start) * 1000.0
        return ExecutionResponse(
            action_id=request.action_id,
            status="unverified",
            error_message=f"UFODesktopBackend: Operation '{request.operation}' unhandled.",
            duration_ms=dur,
        )

    def rollback(self, request: ExecutionRequest, response: ExecutionResponse) -> bool:
        """Polymorphic rollback: Clean up dirty document files created during failed action (HIGH-4)."""
        logger.info("Executing UFODesktopBackend polymorphic rollback for %s", request.action_id)
        cleaned = False
        candidates = [
            request.parameters.get("file_path"),
            request.parameters.get("output_path"),
            response.evidence_data.get("file_path"),
            response.evidence_data.get("docx_path"),
            response.evidence_data.get("pdf_path"),
            response.evidence_data.get("xlsx_path"),
            response.evidence_data.get("csv_path"),
        ]
        for p in candidates:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                    cleaned = True
                except Exception:
                    pass
        return cleaned
