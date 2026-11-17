"""tests/test_verification_engine.py — Comprehensive Test Suite for OPTIMUS Component 3.

Verifies:
1. FilesystemVerifier (existence, size boundaries, SHA-256 hash, extensions, content matches).
2. ProcessVerifier (PID status, application lifecycle, local port socket availability).
3. DataVerifier (JSON schema, CSV tabular integrity, numerical assertions, null checks).
4. CommandVerifier (exit codes, stdout/stderr patterns, prohibited strings, timeouts).
5. EvidenceLedger (tamper-evident SHA-256 hashing, persistence, corruption detection).
6. Postcondition Auto-Inference (from expected_deliverables and tool side_effects).
7. VerificationEngine (subtask verification, critical vs non-critical postconditions).
8. CentralOrchestrator Integration (failed verification blocks completion, evidence ledger linking, memory promotion).
"""

import hashlib
import json
import os
import socket
import sys
from pathlib import Path

# Ensure Mark-LVII-main is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


from core.memory.manager import OptimusMemoryManager
from core.memory.types import MemoryCategory
from core.orchestrator.agents import AgentCapability, AgentProfile, AgentRegistry
from core.orchestrator.events import EventBus, SubtaskVerifiedEvent
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import Subtask, TaskStatus, TaskStore
from core.orchestrator.tools import ToolRegistry
from core.verification.engine import (
    VerificationEngine,
)
from core.verification.ledger import EvidenceLedger
from core.verification.types import (
    Postcondition,
    PostconditionType,
    VerificationRecord,
    VerificationStatus,
)
from core.verification.verifiers import (
    CommandVerifier,
    DataVerifier,
    FilesystemVerifier,
    ProcessVerifier,
)

# ── 1. FilesystemVerifier Tests ──────────────────────────────────────────────

def test_filesystem_verifier_existence_and_non_empty(tmp_path: Path):
    verifier = FilesystemVerifier()
    test_file = tmp_path / "report.txt"
    test_file.write_text("Optimized payload data.", encoding="utf-8")

    # 1. Existing valid file
    pc_pass = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(test_file), "must_exist": True, "non_empty": True},
    )
    rec_pass = verifier.verify("task_1", "st_1", pc_pass)
    assert rec_pass.is_pass is True
    assert rec_pass.status == VerificationStatus.PASS
    assert rec_pass.verify_integrity() is True
    assert rec_pass.actual_observed_result["bytes"] == len("Optimized payload data.")
    assert rec_pass.actual_observed_result["sha256"] == hashlib.sha256(b"Optimized payload data.").hexdigest()

    # 2. Non-existent file
    phantom_file = tmp_path / "missing.txt"
    pc_missing = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(phantom_file), "must_exist": True},
    )
    rec_missing = verifier.verify("task_1", "st_2", pc_missing)
    assert rec_missing.is_pass is False
    assert "Missing deliverable file" in rec_missing.verdict

    # 3. Empty file rejection
    empty_file = tmp_path / "empty.txt"
    empty_file.write_bytes(b"")
    pc_empty = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(empty_file), "non_empty": True},
    )
    rec_empty = verifier.verify("task_1", "st_3", pc_empty)
    assert rec_empty.is_pass is False
    assert "empty" in rec_empty.verdict


def test_filesystem_verifier_hash_extension_and_content(tmp_path: Path):
    verifier = FilesystemVerifier()
    test_file = tmp_path / "data.json"
    content = '{"status": "ok", "value": 42}'
    test_file.write_text(content, encoding="utf-8")
    expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # 1. Matching SHA-256 and extension
    pc_hash_pass = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={
            "path": str(test_file),
            "expected_sha256": expected_sha,
            "expected_extension": ".json",
            "expected_content": '"value": 42',
        },
    )
    rec_hash_pass = verifier.verify("task_1", "st_1", pc_hash_pass)
    assert rec_hash_pass.is_pass is True
    assert "Verified file" in rec_hash_pass.verdict

    # 2. SHA-256 mismatch detection
    pc_hash_fail = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={
            "path": str(test_file),
            "expected_sha256": "0" * 64,
        },
    )
    rec_hash_fail = verifier.verify("task_1", "st_2", pc_hash_fail)
    assert rec_hash_fail.is_pass is False
    assert "SHA-256 hash mismatch" in rec_hash_fail.verdict

    # 3. File extension mismatch
    pc_ext_fail = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={
            "path": str(test_file),
            "expected_extension": ".csv",
        },
    )
    rec_ext_fail = verifier.verify("task_1", "st_3", pc_ext_fail)
    assert rec_ext_fail.is_pass is False
    assert "File extension mismatch" in rec_ext_fail.verdict

    # 4. Missing required content string
    pc_content_fail = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={
            "path": str(test_file),
            "expected_content": "UNEXPECTED_SECRET_KEY",
        },
    )
    rec_content_fail = verifier.verify("task_1", "st_4", pc_content_fail)
    assert rec_content_fail.is_pass is False
    assert "Expected content string not found" in rec_content_fail.verdict


def test_filesystem_verifier_directory_checks(tmp_path: Path):
    verifier = FilesystemVerifier()
    test_dir = tmp_path / "artifacts"
    test_dir.mkdir()
    (test_dir / "f1.txt").write_text("one", encoding="utf-8")
    (test_dir / "f2.txt").write_text("two", encoding="utf-8")

    # 1. Existing directory with min entries
    pc_dir_pass = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={
            "path": str(test_dir),
            "is_directory": True,
            "min_entries": 2,
        },
    )
    rec_dir_pass = verifier.verify("task_1", "st_1", pc_dir_pass)
    assert rec_dir_pass.is_pass is True

    # 2. Too few entries in directory
    pc_dir_fail = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={
            "path": str(test_dir),
            "is_directory": True,
            "min_entries": 5,
        },
    )
    rec_dir_fail = verifier.verify("task_1", "st_2", pc_dir_fail)
    assert rec_dir_fail.is_pass is False
    assert "expected at least 5" in rec_dir_fail.verdict


# ── 2. ProcessVerifier Tests ─────────────────────────────────────────────────

def test_process_verifier_pid_and_socket(tmp_path: Path):
    verifier = ProcessVerifier()

    # 1. Current process PID should be running
    current_pid = os.getpid()
    pc_pid_pass = Postcondition(
        postcondition_type=PostconditionType.PROCESS,
        spec={"pid": current_pid, "must_be_running": True},
    )
    rec_pid_pass = verifier.verify("task_1", "st_1", pc_pid_pass)
    assert rec_pid_pass.is_pass is True
    assert f"PID {current_pid}" in rec_pid_pass.verdict
    assert "running" in rec_pid_pass.verdict

    # 2. Non-existent PID should fail when must_be_running=True
    pc_pid_fail = Postcondition(
        postcondition_type=PostconditionType.PROCESS,
        spec={"pid": 9999999, "must_be_running": True},
    )
    rec_pid_fail = verifier.verify("task_1", "st_2", pc_pid_fail)
    assert rec_pid_fail.is_pass is False

    # 3. Local listening port test
    # Bind an ephemeral local socket
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.bind(("127.0.0.1", 0))
    srv_sock.listen(1)
    assigned_port = srv_sock.getsockname()[1]

    try:
        pc_port_pass = Postcondition(
            postcondition_type=PostconditionType.PROCESS,
            spec={"port": assigned_port, "host": "127.0.0.1", "port_must_be_open": True, "timeout": 0.5},
        )
        rec_port_pass = verifier.verify("task_1", "st_3", pc_port_pass)
        assert rec_port_pass.is_pass is True
        assert "open" in rec_port_pass.verdict
    finally:
        srv_sock.close()

    # 4. Closed port check should fail when port_must_be_open=True
    pc_port_fail = Postcondition(
        postcondition_type=PostconditionType.PROCESS,
        spec={"port": assigned_port, "host": "127.0.0.1", "port_must_be_open": True, "timeout": 0.2},
    )
    rec_port_fail = verifier.verify("task_1", "st_4", pc_port_fail)
    assert rec_port_fail.is_pass is False


# ── 3. DataVerifier Tests ────────────────────────────────────────────────────

def test_data_verifier_json_and_numerical():
    verifier = DataVerifier()

    sample_json = {
        "user_id": "usr_99",
        "score": 95.5,
        "is_active": True,
        "roles": ["admin", "editor"],
    }

    # 1. Valid JSON schema and numerical assertion
    pc_json_pass = Postcondition(
        postcondition_type=PostconditionType.DATA,
        spec={
            "data": sample_json,
            "format": "json",
            "required_fields": ["user_id", "score", "is_active"],
            "field_types": {"user_id": "str", "score": "float", "is_active": "bool"},
            "numerical_assertions": [
                {"field": "score", "operator": "gt", "value": 90},
                {"field": "score", "operator": "between", "min": 90, "max": 100},
            ],
        },
    )
    rec_json_pass = verifier.verify("task_1", "st_1", pc_json_pass)
    assert rec_json_pass.is_pass is True

    # 2. Missing required field
    pc_json_missing = Postcondition(
        postcondition_type=PostconditionType.DATA,
        spec={
            "data": sample_json,
            "format": "json",
            "required_fields": ["user_id", "tenant_id"],
        },
    )
    rec_json_missing = verifier.verify("task_1", "st_2", pc_json_missing)
    assert rec_json_missing.is_pass is False
    assert "Missing required field" in rec_json_missing.verdict

    # 3. Failing numerical assertion
    pc_num_fail = Postcondition(
        postcondition_type=PostconditionType.DATA,
        spec={
            "data": sample_json,
            "numerical_assertions": [
                {"field": "score", "operator": "lt", "value": 50},
            ],
        },
    )
    rec_num_fail = verifier.verify("task_1", "st_3", pc_num_fail)
    assert rec_num_fail.is_pass is False
    assert "Assertion failed: 'score'" in rec_num_fail.verdict


def test_data_verifier_csv_integrity():
    verifier = DataVerifier()

    csv_data = "name,role,level\nAlice,Developer,5\nBob,Architect,8\nCharlie,Analyst,3\n"

    # 1. Valid CSV headers and row count
    pc_csv_pass = Postcondition(
        postcondition_type=PostconditionType.DATA,
        spec={
            "data": csv_data,
            "format": "csv",
            "required_headers": ["name", "role", "level"],
            "min_rows": 3,
            "max_rows": 10,
        },
    )
    rec_csv_pass = verifier.verify("task_1", "st_1", pc_csv_pass)
    assert rec_csv_pass.is_pass is True
    assert rec_csv_pass.actual_observed_result["row_count"] == 3

    # 2. Header missing
    pc_csv_missing_hdr = Postcondition(
        postcondition_type=PostconditionType.DATA,
        spec={
            "data": csv_data,
            "format": "csv",
            "required_headers": ["name", "salary"],
        },
    )
    rec_csv_missing_hdr = verifier.verify("task_1", "st_2", pc_csv_missing_hdr)
    assert rec_csv_missing_hdr.is_pass is False
    assert "Missing required CSV header" in rec_csv_missing_hdr.verdict


# ── 4. CommandVerifier Tests ─────────────────────────────────────────────────

def test_command_verifier():
    verifier = CommandVerifier()

    # 1. Successful command execution
    pc_cmd_pass = Postcondition(
        postcondition_type=PostconditionType.COMMAND,
        spec={
            "command": "python -V",
            "exit_code": 0,
            "actual_stdout": "Python 3.13.1",
            "expected_stdout_pattern": r"Python \d+\.\d+",
        },
    )
    rec_cmd_pass = verifier.verify("task_1", "st_1", pc_cmd_pass)
    assert rec_cmd_pass.is_pass is True

    # 2. Unexpected non-zero exit code
    pc_cmd_fail = Postcondition(
        postcondition_type=PostconditionType.COMMAND,
        spec={
            "command": "false",
            "exit_code": 1,
            "expected_exit_code": 0,
            "actual_stderr": "Command failed",
        },
    )
    rec_cmd_fail = verifier.verify("task_1", "st_2", pc_cmd_fail)
    assert rec_cmd_fail.is_pass is False
    assert "Exit code 1 does not match expected 0" in rec_cmd_fail.verdict

    # 3. Prohibited error string in output
    pc_cmd_prohibited = Postcondition(
        postcondition_type=PostconditionType.COMMAND,
        spec={
            "command": "run_job",
            "exit_code": 0,
            "actual_stderr": "Critical Error: Traceback (most recent call last)",
            "prohibited_patterns": ["Traceback", "Segmentation fault"],
        },
    )
    rec_cmd_prohibited = verifier.verify("task_1", "st_3", pc_cmd_prohibited)
    assert rec_cmd_prohibited.is_pass is False
    assert "prohibited" in rec_cmd_prohibited.verdict.lower()


# ── 5. EvidenceLedger & Tamper-Evident Tests ──────────────────────────────────

def test_evidence_ledger_persistence_and_tamper_detection(tmp_path: Path):
    ledger = EvidenceLedger(storage_root=tmp_path / "evidence")

    # Create and record 2 verification records
    rec1 = VerificationRecord(
        evidence_id="evd_test_001",
        task_id="task_alpha",
        subtask_id="st_1",
        verifier_identity="verifier:filesystem",
        expected_postcondition={"must_exist": True},
        actual_observed_result={"exists": True, "bytes": 1024},
        status=VerificationStatus.PASS,
        verdict="Physical artifact confirmed.",
    )
    rec2 = VerificationRecord(
        evidence_id="evd_test_002",
        task_id="task_alpha",
        subtask_id="st_2",
        verifier_identity="verifier:command",
        expected_postcondition={"exit_code": 0},
        actual_observed_result={"exit_code": 0},
        status=VerificationStatus.PASS,
        verdict="Exit code 0 confirmed.",
    )

    ledger.record(rec1)
    ledger.record(rec2)

    # 1. Querying ledger
    assert ledger.get("evd_test_001") is not None
    assert len(ledger.list_for_task("task_alpha")) == 2
    assert len(ledger.list_for_subtask("st_1")) == 1

    # 2. Check initial ledger integrity
    is_valid, corrupted = ledger.verify_ledger_integrity()
    assert is_valid is True
    assert len(corrupted) == 0

    # 3. Simulate tamper: reload from disk and verify persistence
    ledger2 = EvidenceLedger(storage_root=tmp_path / "evidence")
    assert len(ledger2.list_for_task("task_alpha")) == 2

    # 4. Tamper with record on disk (malicious modification of verdict)
    ledger_file = tmp_path / "evidence" / "evidence.json"
    data = json.loads(ledger_file.read_text(encoding="utf-8"))
    # Alter verdict in record 1 without recomputing cryptographic integrity hash
    data[0]["verdict"] = "TAMPERED_VERDICT_BY_MALICIOUS_AGENT"
    ledger_file.write_text(json.dumps(data), encoding="utf-8")

    # Reload ledger and verify tamper detection triggers
    ledger_tampered = EvidenceLedger(storage_root=tmp_path / "evidence")
    is_valid_after_tamper, corrupted_ids = ledger_tampered.verify_ledger_integrity()
    assert is_valid_after_tamper is False
    assert any("evd_test_001" in item for item in corrupted_ids)


# ── 6. VerificationEngine & Auto-Inference Tests ──────────────────────────────

def test_verification_engine_auto_inference_and_subtask(tmp_path: Path):
    engine = VerificationEngine(storage_root=tmp_path / "verif_engine")

    test_file = tmp_path / "final_report.txt"
    test_file.write_text("All data processed cleanly.", encoding="utf-8")

    st = Subtask(
        subtask_id="st_auto_1",
        task_id="task_auto",
        index=1,
        title="Generate Final Report",
        description="Write output to final_report.txt",
        assigned_agent="filesystem",
        expected_deliverables=[str(test_file)],
    )

    side_effects = [
        {
            "tool_name": "write_file",
            "arguments": {"path": str(test_file), "content": "All data processed cleanly."},
            "status": "done",
            "output": f"Successfully wrote {len('All data processed cleanly.')} bytes",
        }
    ]

    all_passed, verdict, records, evidence_map = engine.verify_subtask(
        subtask=st,
        side_effects=side_effects,
        output_summary="Saved final_report.txt",
    )

    assert all_passed is True
    assert "PASS" in verdict
    assert len(records) >= 1
    assert "final_report.txt" in evidence_map
    assert evidence_map["final_report.txt"]["bytes"] == len("All data processed cleanly.")
    assert len(evidence_map["final_report.txt"]["sha256"]) == 64

    # Verification records must be persisted in ledger
    task_evds = engine.get_task_evidence("task_auto")
    assert len(task_evds) >= 1


def test_verification_engine_multiple_postconditions(tmp_path: Path):
    engine = VerificationEngine(storage_root=tmp_path / "multi_pc")

    test_file = tmp_path / "summary.csv"
    test_file.write_text("item,qty\napples,10\noranges,20\n", encoding="utf-8")

    # Construct subtask with mixed postconditions: Filesystem + Data
    st = Subtask(
        subtask_id="st_multi",
        task_id="task_multi",
        index=1,
        title="Produce and validate inventory CSV",
        description="Write inventory CSV and validate row count",
        assigned_agent="general",
    )

    pc1 = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(test_file), "must_exist": True, "expected_extension": ".csv"},
        critical=True,
    )
    pc2 = Postcondition(
        postcondition_type=PostconditionType.DATA,
        spec={"data": test_file.read_text(encoding="utf-8"), "format": "csv", "min_rows": 2},
        critical=True,
    )
    # Non-critical postcondition that fails
    pc3_noncrit = Postcondition(
        postcondition_type=PostconditionType.DATA,
        spec={"data": test_file.read_text(encoding="utf-8"), "format": "csv", "min_rows": 100},
        critical=False,
    )

    all_passed, verdict, records, _evidence_map = engine.verify_subtask(
        subtask=st,
        extra_postconditions=[pc1, pc2, pc3_noncrit],
    )

    # Critical conditions passed, non-critical failed -> overall passed!
    assert all_passed is True
    assert len(records) == 3
    assert any("[FAIL]" in v for v in verdict.split(" | "))
    assert any("[PASS]" in v for v in verdict.split(" | "))


# ── 7. CentralOrchestrator Integration Tests ─────────────────────────────────

def test_orchestrator_failed_verification_blocks_task(tmp_path: Path):
    """Verify that an agent claiming success with a missing deliverable fails immediately."""
    store = TaskStore(tmp_path / "tasks")
    bus = EventBus()
    agent_reg = AgentRegistry()
    tool_reg = ToolRegistry()
    mem_mgr = OptimusMemoryManager(storage_root=tmp_path / "memory")
    verif_engine = VerificationEngine(storage_root=tmp_path / "verification")

    # Create dummy agent
    agent_reg.register(AgentProfile(
        name="filesystem",
        title="Filesystem Worker",
        description="Test worker",
        capability=AgentCapability(domain="filesystem", allowed_tools=["read_file", "write_file"]),
    ))

    phantom_file = tmp_path / "phantom_output.txt"

    def mock_brain(system, turns, schemas=None):
        return {"text": "I have successfully created phantom_output.txt!", "calls": []}

    orchestrator = CentralOrchestrator(
        agent_registry=agent_reg,
        tool_registry=tool_reg,
        task_store=store,
        event_bus=bus,
        memory_manager=mem_mgr,
        verification_engine=verif_engine,
        brain_fn=mock_brain,
        allowed_roots=[str(tmp_path)],
    )

    verified_events: list[SubtaskVerifiedEvent] = []
    bus.subscribe(SubtaskVerifiedEvent, lambda e: verified_events.append(e))

    st_fail = Subtask(
        subtask_id="st_phantom_1",
        task_id="task_fail",
        index=1,
        title="Generate Phantom Deliverable",
        description="Phantom output",
        assigned_agent="filesystem",
        expected_deliverables=[str(phantom_file)],
    )

    # 1. Independent verification rejects missing physical file
    is_ok, verdict, _evidence_map = orchestrator._verify_deliverables(
        subtask=st_fail,
        output_summary="Agent falsely claimed success without creating file.",
    )
    assert is_ok is False
    assert "Missing deliverable file" in verdict

    # 2. Orchestrated execution with failing physical deliverable
    # Goal triggers deterministic filesystem subtask
    goal = f"Write output to {phantom_file.name}"
    task = orchestrator.execute_task(goal, task_id="task_fail")

    # Verification must fail and task must be marked FAILED
    assert task.status == TaskStatus.FAILED
    assert "Verification failed" in task.error or "Missing deliverable file" in task.error
    assert len(verified_events) >= 1
    assert any(e.is_verified is False for e in verified_events)

    # Unverified deliverable must NOT be promoted to Semantic Memory
    semantic_entries = mem_mgr.store.list_category(MemoryCategory.SEMANTIC)
    assert not any("phantom_output.txt" in e.content for e in semantic_entries)


def test_orchestrator_successful_verification_promotes_deliverable(tmp_path: Path):
    """Verify that valid physical files are verified, recorded in ledger, and promoted to Semantic Memory."""
    store = TaskStore(tmp_path / "tasks")
    bus = EventBus()
    agent_reg = AgentRegistry()
    tool_reg = ToolRegistry()
    mem_mgr = OptimusMemoryManager(storage_root=tmp_path / "memory")
    verif_engine = VerificationEngine(storage_root=tmp_path / "verification")

    real_file = tmp_path / "real_output.txt"
    real_file.write_text("Physical verified contents.", encoding="utf-8")

    orchestrator = CentralOrchestrator(
        agent_registry=agent_reg,
        tool_registry=tool_reg,
        task_store=store,
        event_bus=bus,
        memory_manager=mem_mgr,
        verification_engine=verif_engine,
        allowed_roots=[str(tmp_path)],
    )

    st_pass = Subtask(
        subtask_id="st_real_1",
        task_id="task_success",
        index=1,
        title="Physical File Write",
        description="Verify real deliverable",
        assigned_agent="general",
        expected_deliverables=[str(real_file)],
    )

    # Verify deliverables directly through orchestrator
    is_ok, verdict, evidence_map = orchestrator._verify_deliverables(
        subtask=st_pass,
        output_summary="Deliverable written.",
    )

    assert is_ok is True
    assert "SHA-256" in verdict
    assert "real_output.txt" in evidence_map
    ev_id = evidence_map["real_output.txt"]["evidence_id"]
    assert ev_id.startswith("evd_")

    # Manually promote through context_bus as execute_task would
    mem_mgr.context_bus.publish_deliverable(
        task_id="task_success",
        subtask_id="st_real_1",
        agent_name="general",
        file_path=str(real_file),
        byte_size=len("Physical verified contents."),
        sha256_hash=evidence_map["real_output.txt"]["sha256"],
        verdict=verdict,
        evidence_id=ev_id,
    )

    # Deliverable must now reside in trusted Semantic Memory with evidence ID tag
    semantic_entries = mem_mgr.store.list_category(MemoryCategory.SEMANTIC)
    matching = [e for e in semantic_entries if "real_output.txt" in e.content]
    assert len(matching) == 1
    assert matching[0].is_verified is True
    assert ev_id in matching[0].tags
    assert matching[0].metadata["evidence_id"] == ev_id

    # Ledger must verify with 100% cryptographic integrity
    is_valid, _ = verif_engine.verify_ledger_integrity()
    assert is_valid is True
