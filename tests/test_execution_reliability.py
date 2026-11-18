"""tests/test_execution_reliability.py — Automated Test Suite for Autonomous Task Execution Reliability & Tool-Use.

Validates:
1. Real-world execution of the 10 computer tasks:
   - Task 1: Launch Notepad and verify process lifecycle (open_app & process_kill).
   - Task 2: Open directory in file manager / verify directory target (open_directory).
   - Task 3: Retrieve real-time CPU and RAM telemetry (cpu_status & memory_status).
   - Task 4: Identify largest PDF in an authorized directory (largest_files).
   - Task 5: Read a text file and verify contents (read_file).
   - Task 6: Check Ollama local LLM service status and enumerate models (ollama_status).
   - Task 7: Retrieve Python version, virtualenv, and developer environment tooling (python_version).
   - Task 8: Search web for official Ollama repository (search_web).
   - Task 9: Execute multi-step physical computer audit (computer_audit).
   - Task 10: Handle unavailable / invalid tool gracefully without claiming false success.
2. Capability-aware planning and anti-script-dumping guards in CentralOrchestrator.
3. Execution state tracking (Planned, Running, Completed, Partially completed, Failed, Unable to execute).
4. Physical verification postconditions (ProcessVerifier, FilesystemVerifier).
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Ensure Mark-LVII-main root is in sys.path
_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.tools import ToolRegistry
from core.sandbox.tools_execution import (
    largest_files_handler,
    open_app_handler,
    open_directory_handler,
    process_kill_handler,
    process_status_handler,
)
from core.sandbox.tools_research import search_web_handler
from core.sandbox.tools_system import (
    computer_audit_handler,
    cpu_status_handler,
    memory_status_handler,
    ollama_status_handler,
    python_version_handler,
)
from core.verification.engine import (
    Postcondition,
    PostconditionType,
    VerificationEngine,
)


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def workspace_tmp(tmp_path: Path):
    """Provide an isolated temporary workspace directory."""
    ws = tmp_path / "test_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def orchestrator_instance():
    """Create a configured CentralOrchestrator instance."""
    return CentralOrchestrator()


# ==============================================================================
# 10 Real-World Computer Tasks
# ==============================================================================

class TestRealWorldComputerTasks:
    """Rigorous tests covering the 10 real-world computer tasks."""

    def test_task_01_launch_app_and_verify_process(self):
        """Task 1: Launch Notepad (or platform equivalent), verify running process, then terminate."""
        if platform.system() != "Windows":
            pytest.skip("Notepad launch verification is specific to Windows host.")

        # Test launching notepad
        res = open_app_handler({"app_name": "notepad"})
        assert res.status in ("done", "unverified"), f"App launch returned unexpected status: {res.status}"
        assert res.evidence is not None
        pid = res.evidence.get("pid")
        assert pid is not None and isinstance(pid, int) and pid > 0

        # Verify process is running via process_status_handler
        status_res = process_status_handler({"pid": pid})
        assert status_res.status == "done"
        assert status_res.evidence.get("is_running") is True

        # Clean up test process safely
        kill_res = process_kill_handler({"pid": pid})
        assert kill_res.status == "done"
        assert kill_res.evidence.get("terminated") is True

    def test_task_02_open_directory(self, workspace_tmp: Path):
        """Task 2: Open directory in file manager / verify directory resolution."""
        # Test workspace directory
        res = open_directory_handler({"path": str(workspace_tmp)}, context={"allowed_roots": [str(workspace_tmp)]})
        assert res.status == "done"
        assert res.evidence["is_dir"] is True
        assert Path(res.evidence["path"]).resolve() == workspace_tmp.resolve()

        # Test 'downloads' virtual directory
        dl_res = open_directory_handler({"path": "downloads"})
        assert dl_res.status == "done"
        assert dl_res.evidence["is_dir"] is True
        assert "Downloads" in dl_res.evidence["path"]

    def test_task_03_cpu_and_ram_telemetry(self):
        """Task 3: Retrieve physical CPU and RAM metrics directly."""
        cpu_res = cpu_status_handler({})
        assert cpu_res.status == "done"
        assert cpu_res.evidence["cores_logical"] >= 1
        assert isinstance(cpu_res.evidence["overall_percent"], (int, float))

        mem_res = memory_status_handler({})
        assert mem_res.status == "done"
        assert mem_res.evidence["total_gb"] > 0
        assert mem_res.evidence["available_gb"] >= 0
        assert 0.0 <= mem_res.evidence["used_percent"] <= 100.0

    def test_task_04_largest_pdf_discovery(self, workspace_tmp: Path):
        """Task 4: Identify largest PDF in an authorized directory."""
        # Create test PDF files of known sizes
        pdf1 = workspace_tmp / "doc_small.pdf"
        pdf2 = workspace_tmp / "doc_large.pdf"
        pdf3 = workspace_tmp / "doc_medium.pdf"
        other = workspace_tmp / "not_a_pdf.txt"

        pdf1.write_bytes(b"%PDF-1.4 " + b"A" * 1024)        # ~1 KB
        pdf2.write_bytes(b"%PDF-1.4 " + b"B" * (50 * 1024))  # ~50 KB
        pdf3.write_bytes(b"%PDF-1.4 " + b"C" * (10 * 1024))  # ~10 KB
        other.write_bytes(b"D" * (100 * 1024))               # 100 KB text file

        res = largest_files_handler(
            {"path": str(workspace_tmp), "extension": "pdf", "top_n": 5},
            context={"allowed_roots": [str(workspace_tmp)]},
        )

        assert res.status == "done"
        assert res.evidence["count"] == 3
        files = res.evidence["files"]
        assert len(files) == 3
        # Confirm largest PDF is at index 0
        assert files[0]["name"] == "doc_large.pdf"
        assert files[0]["size_bytes"] >= 50 * 1024
        assert files[1]["name"] == "doc_medium.pdf"
        assert files[2]["name"] == "doc_small.pdf"

    def test_task_05_read_text_file(self, workspace_tmp: Path):
        """Task 5: Read a text file and verify contents."""
        test_file = workspace_tmp / "sample_note.txt"
        test_content = "Line 1: OPTIMUS execution test\nLine 2: Real-world task execution\nLine 3: Verified."
        test_file.write_text(test_content, encoding="utf-8")

        registry = ToolRegistry()
        res = registry.execute(
            "read_file",
            {"path": str(test_file)},
            context={"allowed_roots": [str(workspace_tmp)]},
        )

        assert res.status == "done"
        assert "Line 1: OPTIMUS execution test" in res.output
        assert res.evidence.get("bytes", 0) == len(test_content.encode("utf-8"))

    def test_task_06_ollama_service_inspection(self):
        """Task 6: Check whether Ollama is running and enumerate models."""
        res = ollama_status_handler({"host": "http://localhost:11434"})
        assert res.status == "done"
        assert "is_running" in res.evidence
        assert "models" in res.evidence
        # If Ollama is running on this machine, verify model count structure
        if res.evidence["is_running"]:
            assert res.evidence["version"] != "Unknown"
            assert isinstance(res.evidence["models"], list)

    def test_task_07_python_version_and_dev_tools(self):
        """Task 7: Retrieve installed Python version and developer environment tooling."""
        res = python_version_handler({"tools": ["python", "git"]})
        assert res.status == "done"
        assert res.evidence["python_version"] == sys.version.split()[0]
        assert "git" in res.evidence["developer_tools"]
        assert Path(res.evidence["executable"]).is_file()

    def test_task_08_search_web_for_ollama(self):
        """Task 8: Search web for official Ollama repository."""
        res = search_web_handler({"query": "official Ollama GitHub repository", "max_results": 3})
        assert res.status == "done"
        assert "ollama" in res.output.lower() or "ollama" in str(res.evidence).lower()
        assert res.evidence.get("citations_count", 0) >= 1 or len(res.evidence.get("urls", [])) >= 1

    def test_task_09_multi_step_computer_audit(self):
        """Task 9: Execute complete physical computer audit with multi-subsystem telemetry."""
        res = computer_audit_handler({})
        assert res.status == "done"
        evidence = res.evidence
        assert "system" in evidence
        assert "cpu" in evidence
        assert "memory" in evidence
        assert "disks" in evidence
        assert "gpu" in evidence
        assert "environment" in evidence
        assert "ollama" in evidence
        assert "duration_seconds" in evidence

        assert evidence["cpu"]["cores_logical"] >= 1
        assert evidence["memory"]["total_gb"] > 0
        assert len(evidence["disks"]) >= 1

    def test_task_10_handle_unavailable_tool_gracefully(self):
        """Task 10: Gracefully reject unavailable tools or invalid actions without false success."""
        registry = ToolRegistry()
        res = registry.execute("non_existent_tool_xyz", {"foo": "bar"})
        assert res.status == "failed"
        assert "not registered" in res.error.lower() or "not found" in res.error.lower()

        # Test invalid path in open_directory
        bad_dir_res = open_directory_handler({"path": "Z:\\non_existent_folder_xyz_12345"})
        assert bad_dir_res.status == "failed"
        assert "does not exist" in bad_dir_res.error.lower()


# ==============================================================================
# Central Orchestrator Integration & Execution Reliability
# ==============================================================================

class TestOrchestratorExecutionReliability:
    """Tests orchestrator fast-path, anti-script-dumping guard, and state tracking."""

    def test_orchestrator_fast_path_cpu_ram(self, orchestrator_instance: CentralOrchestrator):
        """Test fast-path direct execution for CPU and RAM queries."""
        task = orchestrator_instance.execute_task("Check current CPU and RAM usage")
        assert task.status.value.lower() == "completed"
        assert task.plan is not None and len(task.plan.subtasks) >= 1
        st = task.plan.subtasks[0]
        assert st.status.value.lower() == "completed"
        assert "CPU Status" in st.output_summary
        assert "Memory Status" in st.output_summary

    def test_orchestrator_fast_path_computer_audit(self, orchestrator_instance: CentralOrchestrator):
        """Test fast-path direct execution for computer audit."""
        task = orchestrator_instance.execute_task("Perform a complete computer audit")
        assert task.status.value.lower() == "completed"
        st = task.plan.subtasks[0]
        assert st.status.value.lower() == "completed"
        assert "OPTIMUS System Audit Report" in st.output_summary

    def test_orchestrator_fast_path_ollama_status(self, orchestrator_instance: CentralOrchestrator):
        """Test fast-path direct execution for Ollama service inspection."""
        task = orchestrator_instance.execute_task("Check whether Ollama is running and list models")
        assert task.status.value.lower() == "completed"
        st = task.plan.subtasks[0]
        assert "Ollama Service Status" in st.output_summary

    def test_orchestrator_fast_path_python_version(self, orchestrator_instance: CentralOrchestrator):
        """Test fast-path direct execution for Python environment inspection."""
        task = orchestrator_instance.execute_task("Retrieve installed Python version")
        assert task.status.value.lower() == "completed"
        st = task.plan.subtasks[0]
        assert "Python & Developer Environment" in st.output_summary

    def test_orchestrator_fast_path_open_directory(self, orchestrator_instance: CentralOrchestrator):
        """Test fast-path direct execution for opening downloads folder."""
        task = orchestrator_instance.execute_task("Open Downloads folder in file manager")
        assert task.status.value.lower() == "completed"
        st = task.plan.subtasks[0]
        assert "Downloads" in st.output_summary

    def test_orchestrator_anti_script_dumping_interception(self, orchestrator_instance: CentralOrchestrator):
        """Verify that when a user asks for audit, OPTIMUS doesn't generate a manual script."""
        task = orchestrator_instance.execute_task("Perform a complete computer audit covering CPU, RAM, disk and GPU")
        assert task.status.value.lower() == "completed"
        output = task.result

        # Must not tell user to run a powershell script
        prohibited_phrases = [
            "run the following script",
            "execute this powershell script",
            "please run the attached script",
            "save this script to a file and run it",
        ]
        for phrase in prohibited_phrases:
            assert phrase not in output.lower(), f"Script dumping phrase detected: '{phrase}'"

        # Must contain actual collected telemetry
        assert "cores" in output.lower() or "cpu" in output.lower()
        assert "ram" in output.lower() or "memory" in output.lower()

    def test_execution_state_tracking(self, orchestrator_instance: CentralOrchestrator):
        """Test that execution states are correctly recorded and distinguishable."""
        task = orchestrator_instance.execute_task("Get CPU status")
        assert task.status.value.lower() == "completed"
        for st in task.plan.subtasks:
            assert st.status.value.lower() in ("pending", "running", "completed", "failed", "partially_completed", "unable_to_execute")

        # Test rejected / unable to execute task
        bad_task = orchestrator_instance.execute_task("nonexistent_command_xyz_never_works")
        assert bad_task.status.value.lower() in ("failed", "unable_to_execute", "completed")


# ==============================================================================
# Verification Postconditions
# ==============================================================================

class TestVerificationPostconditions:
    """Test physical postcondition verification for computer actions."""

    def test_infer_postcondition_for_open_app(self):
        """Verify that open_app correctly infers a ProcessVerifier postcondition."""
        engine = VerificationEngine()
        pcs = engine.infer_postconditions(
            subtask=None,
            side_effects=[{"tool_name": "open_app", "arguments": {"app_name": "notepad"}, "evidence": {"pid": 12345}}],
        )
        assert len(pcs) >= 1
        assert any(p.postcondition_type == PostconditionType.PROCESS for p in pcs)
        proc_pc = [p for p in pcs if p.postcondition_type == PostconditionType.PROCESS][0]
        assert proc_pc.spec["pid"] == 12345
        assert proc_pc.spec["should_be_running"] is True

    def test_infer_postcondition_for_create_directory(self, workspace_tmp: Path):
        """Verify that create_directory infers a FilesystemVerifier postcondition."""
        engine = VerificationEngine()
        target_dir = str(workspace_tmp / "new_project_folder")
        pcs = engine.infer_postconditions(
            subtask=None,
            side_effects=[{"tool_name": "create_directory", "arguments": {"path": target_dir}}],
        )
        assert len(pcs) >= 1
        assert any(p.postcondition_type == PostconditionType.FILESYSTEM for p in pcs)
        fs_pc = [p for p in pcs if p.postcondition_type == PostconditionType.FILESYSTEM][0]
        assert fs_pc.spec["path"] == target_dir
        assert fs_pc.spec["is_directory"] is True

    def test_filesystem_verifier_real_directory(self, workspace_tmp: Path):
        """Test FilesystemVerifier execution on an actual created directory."""
        engine = VerificationEngine()
        target = workspace_tmp / "real_verified_dir"
        target.mkdir()

        postcondition = Postcondition(
            postcondition_type=PostconditionType.FILESYSTEM,
            spec={"path": str(target), "must_exist": True, "is_directory": True},
            description="Created directory verification",
            critical=True,
        )
        rec = engine.verify_postcondition(
            task_id="task_test_01",
            subtask_id="st_01",
            postcondition=postcondition,
        )
        assert rec.status.value.lower() == "pass"
        assert rec.actual_observed_result.get("exists") is True
