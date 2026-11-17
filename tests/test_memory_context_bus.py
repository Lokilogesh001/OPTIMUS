"""tests/test_memory_context_bus.py — Test Suite for OPTIMUS Memory & Context Bus.

Verifies:
1. Memory persistence and retrieval (CRUD, atomic file persistence across categories).
2. Memory classification (WORKING, EPISODIC, SEMANTIC, PROCEDURAL).
3. Cross-subtask context sharing and dependency propagation.
4. Task context isolation (preventing inter-task memory leaks).
5. Role-based memory access permissions (preventing unauthorized agents from seeing restricted context).
6. Adaptive context window management, token estimation, and compaction.
7. Strict preservation of verified artifacts, file paths, and cryptographic hashes during compaction.
8. CentralOrchestrator end-to-end integration with memory promotion of verified deliverables.
9. Backward compatibility with Mark-LVII's existing memory_manager.
10. Action tool integration (optimus_orchestrator memory_search and memory_store).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure Mark-LVII-main is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.memory.context_bus import SharedContextBus
from core.memory.manager import OptimusMemoryManager
from core.memory.store import MemoryStore
from core.memory.types import (
    ContextEnvelope,
    MemoryCategory,
    MemoryEntry,
    new_memory_id,
)
from core.memory.window_manager import (
    ContextWindowManager,
    compact_text,
    estimate_chars,
    estimate_tokens,
)
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import (
    TaskStatus,
    TaskStore,
)

# ============================================================================
# 1. Memory Store & Classification Tests
# ============================================================================

def test_memory_store_crud_and_persistence(tmp_path):
    storage_dir = tmp_path / "memory_store"
    store = MemoryStore(storage_dir, sync_legacy=False)

    # 1. Create and Put
    entry = MemoryEntry(
        entry_id=new_memory_id("sem"),
        category=MemoryCategory.SEMANTIC,
        key="db_port",
        content="Database runs on port 5432",
        source="orchestrator",
        is_verified=True,
        tags=["config", "database"],
    )
    store.put(entry)

    # 2. Get by ID and Key
    retrieved = store.get(entry.entry_id)
    assert retrieved is not None
    assert retrieved.key == "db_port"
    assert retrieved.content == "Database runs on port 5432"
    assert retrieved.is_verified is True

    retrieved_by_key = store.get_by_key(MemoryCategory.SEMANTIC, "db_port")
    assert retrieved_by_key is not None
    assert retrieved_by_key.entry_id == entry.entry_id

    # 3. Update
    updated = store.update(entry.entry_id, {"content": "Database runs on port 5433"})
    assert updated is not None
    assert updated.content == "Database runs on port 5433"

    # 4. Verify disk persistence across new instance
    store2 = MemoryStore(storage_dir, sync_legacy=False)
    loaded = store2.get_by_key(MemoryCategory.SEMANTIC, "db_port")
    assert loaded is not None
    assert loaded.content == "Database runs on port 5433"

    # 5. Delete
    ok = store2.delete(entry.entry_id)
    assert ok is True
    assert store2.get(entry.entry_id) is None


def test_memory_classification_categories(tmp_path):
    store = MemoryStore(tmp_path / "categories", sync_legacy=False)

    e_work = MemoryEntry(
        entry_id=new_memory_id("wrk"),
        category=MemoryCategory.WORKING,
        key="temp_calc",
        content="Intermediate result = 42",
        task_id="t-1",
    )
    e_episodic = MemoryEntry(
        entry_id=new_memory_id("epi"),
        category=MemoryCategory.EPISODIC,
        key="task_t-0",
        content="Task t-0 completed in 2.1s",
        task_id="t-0",
    )
    e_semantic = MemoryEntry(
        entry_id=new_memory_id("sem"),
        category=MemoryCategory.SEMANTIC,
        key="app_version",
        content="OPTIMUS v1.0",
        is_verified=True,
    )
    e_procedural = MemoryEntry(
        entry_id=new_memory_id("prc"),
        category=MemoryCategory.PROCEDURAL,
        key="calc_workflow",
        content="Decompose arithmetic before computing",
    )

    store.put(e_work)
    store.put(e_episodic)
    store.put(e_semantic)
    store.put(e_procedural)

    assert len(store.list_category(MemoryCategory.WORKING)) == 1
    assert len(store.list_category(MemoryCategory.EPISODIC)) == 1
    assert len(store.list_category(MemoryCategory.SEMANTIC)) == 1
    assert len(store.list_category(MemoryCategory.PROCEDURAL)) == 1

    # Clear working memory scoped to task t-1
    cleared = store.clear_working(task_id="t-1")
    assert cleared == 1
    assert len(store.list_category(MemoryCategory.WORKING)) == 0
    # Other categories must remain unaffected
    assert len(store.list_category(MemoryCategory.SEMANTIC)) == 1


def test_memory_search_and_ranking(tmp_path):
    store = MemoryStore(tmp_path / "search", sync_legacy=False)

    store.put(MemoryEntry(
        entry_id=new_memory_id("sem"),
        category=MemoryCategory.SEMANTIC,
        key="server_ip",
        content="Production server is hosted at 10.0.0.15",
        tags=["network", "prod"],
        is_verified=True,
    ))
    store.put(MemoryEntry(
        entry_id=new_memory_id("sem"),
        category=MemoryCategory.SEMANTIC,
        key="staging_ip",
        content="Staging test host is at 10.0.0.99",
        tags=["network", "staging"],
        is_verified=False,
    ))

    # Search for "production server"
    matches = store.search("production server")
    assert len(matches) >= 1
    assert matches[0].key == "server_ip"
    # Verified entry gets higher ranking
    assert matches[0].is_verified is True


# ============================================================================
# 2. Shared Context Bus & Access Permissions Tests
# ============================================================================

def test_context_bus_cross_subtask_sharing(tmp_path):
    store = MemoryStore(tmp_path / "bus_share", sync_legacy=False)
    bus = SharedContextBus(store)

    task_id = "task_pipeline_1"
    bus.isolate_task(task_id)

    # Subtask 1 publishes output
    bus.publish_subtask_output(
        task_id=task_id,
        subtask_id="st_1",
        agent_name="calculator",
        key="sum_result",
        output="Sum = 120",
        is_verified=True,
        verification_verdict="Arithmetic exact",
    )

    # Subtask 2 creates envelope requesting prerequisite st_1
    envelope = bus.create_envelope(
        task_id=task_id,
        subtask_id="st_2",
        agent_name="filesystem",
        goal="Calculate sum and save to file",
        subtask_title="Save result",
        subtask_description="Write calculation to disk",
        prerequisite_subtask_ids=["st_1"],
    )

    assert "st_1" in envelope.prerequisite_results
    assert envelope.prerequisite_results["st_1"]["output"] == "Sum = 120"
    assert envelope.prerequisite_results["st_1"]["is_verified"] is True


def test_context_bus_task_isolation(tmp_path):
    store = MemoryStore(tmp_path / "bus_iso", sync_legacy=False)
    bus = SharedContextBus(store)

    # Task A publishes internal notes
    bus.isolate_task("task_A")
    bus.publish_subtask_output(
        task_id="task_A",
        subtask_id="st_A1",
        agent_name="researcher",
        key="secret_note",
        output="Classified discovery in Task A",
    )

    # Task B creates envelope
    bus.isolate_task("task_B")
    envelope_b = bus.create_envelope(
        task_id="task_B",
        subtask_id="st_B1",
        agent_name="researcher",
        goal="Unrelated work in Task B",
        subtask_title="Step B",
        subtask_description="Search data",
    )

    # Task B must NOT see Task A's prerequisite outputs
    assert "st_A1" not in envelope_b.prerequisite_results
    task_b_ctx = bus.get_task_context("task_B")
    assert "st_A1" not in task_b_ctx.get("subtask_outputs", {})


def test_context_bus_role_based_access_permissions(tmp_path):
    store = MemoryStore(tmp_path / "bus_rbac", sync_legacy=False)
    bus = SharedContextBus(store)

    task_id = "task_security"
    bus.isolate_task(task_id)

    # Output restricted to 'filesystem' and 'orchestrator'
    bus.publish_subtask_output(
        task_id=task_id,
        subtask_id="st_sec",
        agent_name="filesystem",
        key="disk_tokens",
        output="Confidential filesystem token",
        access_roles=["filesystem", "orchestrator"],
    )

    # 1. Unauthorized agent 'calculator' creates envelope
    calc_envelope = bus.create_envelope(
        task_id=task_id,
        subtask_id="st_calc",
        agent_name="calculator",
        goal="Compute metrics",
        subtask_title="Arithmetic",
        subtask_description="Calculate numbers",
    )
    # Blocked: calculator cannot access filesystem-restricted context
    assert "st_sec" not in calc_envelope.prerequisite_results

    # 2. Authorized agent 'filesystem' creates envelope
    fs_envelope = bus.create_envelope(
        task_id=task_id,
        subtask_id="st_fs",
        agent_name="filesystem",
        goal="File management",
        subtask_title="Write data",
        subtask_description="Save token",
    )
    # Allowed: filesystem agent receives it
    assert "st_sec" in fs_envelope.prerequisite_results


# ============================================================================
# 3. Context Window Management & Compaction Tests
# ============================================================================

def test_context_estimation_and_compaction():
    text = "Short text"
    tokens = estimate_tokens(text)
    assert tokens >= 1
    chars = estimate_chars(tokens)
    assert chars >= len(text) // 2

    # Long text compaction
    long_raw = "Start of story " + ("word " * 100) + " End of story"
    compacted_unver = compact_text(long_raw, max_chars=80, is_verified=False)
    assert len(compacted_unver) < len(long_raw)
    assert "compacted" in compacted_unver
    assert "Start of story" in compacted_unver
    assert "End of story" in compacted_unver

    # Verified content preservation marker
    compacted_ver = compact_text(long_raw, max_chars=80, is_verified=True)
    assert "VERIFIED CONTENT COMPACTED" in compacted_ver


def test_verified_artifacts_never_altered_during_compaction():
    wm = ContextWindowManager(default_max_tokens=200)

    envelope = ContextEnvelope(
        task_id="t-artifact",
        subtask_id="st-2",
        agent_name="filesystem",
        goal="Audit logs and persist deliverable",
        subtask_title="Verify and save",
        subtask_description="Generate audit file",
        verified_deliverables=[{
            "file_path": "/var/log/audit.sha256",
            "byte_size": 4096,
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "verdict": "Confirmed on disk",
        }],
        prerequisite_results={
            "st-1": {
                "agent": "researcher",
                "output": "Extremely verbose intermediate findings " * 50,
                "is_verified": True,
            }
        },
    )

    rendered = wm.format_envelope_for_prompt(envelope, max_tokens=150)

    # 1. Deliverable path and exact SHA-256 hash must be 100% intact
    assert "/var/log/audit.sha256" in rendered
    assert "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" in rendered
    assert "4096 bytes" in rendered

    # 2. Prerequisite results must be compacted to fit budget
    assert len(rendered) < estimate_chars(250)


# ============================================================================
# 4. Orchestrator Integration & Deliverable Promotion Tests
# ============================================================================

def test_orchestrator_memory_promotion_flow(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage_orch = tmp_path / "orch_storage"
    storage_mem = tmp_path / "optimus_mem"

    mem_manager = OptimusMemoryManager(storage_root=storage_mem, sync_legacy=False)

    orchestrator = CentralOrchestrator(
        task_store=TaskStore(storage_orch),
        allowed_roots=[str(workspace)],
        memory_manager=mem_manager,
    )

    # Execute deterministic multi-step task
    goal = "Calculate 25 * 4 and write report to summary.txt"
    record = orchestrator.run_task(goal)

    assert record.status == TaskStatus.COMPLETED

    # 1. Check that verified deliverable was promoted to SEMANTIC memory
    semantic_entries = mem_manager.store.list_category(MemoryCategory.SEMANTIC)
    assert len(semantic_entries) >= 1
    deliverable_entry = next((e for e in semantic_entries if "deliverable" in e.tags), None)
    assert deliverable_entry is not None
    assert deliverable_entry.is_verified is True
    assert deliverable_entry.confidence == 1.0
    assert "summary.txt" in deliverable_entry.content

    # 2. Check that completed task was recorded in EPISODIC memory
    episodic_entries = mem_manager.store.list_category(MemoryCategory.EPISODIC)
    assert len(episodic_entries) >= 1
    assert any(record.task_id in e.key for e in episodic_entries)


# ============================================================================
# 5. Backward Compatibility with Mark-LVII Memory Tests
# ============================================================================

def test_backward_compatibility_with_mark_lvii_memory():
    from memory import memory_manager

    # Test existing Mark-LVII memory functions
    res = memory_manager.remember("favorite_framework", "PyTorch", category="preferences")
    assert "Remembered:" in res
    assert "PyTorch" in res

    recalled = memory_manager.search_memory("framework")
    assert "PyTorch" in recalled

    # Test forget
    forget_res = memory_manager.forget("favorite_framework", category="preferences")
    assert "Forgotten:" in forget_res


# ============================================================================
# 6. Action Tool Memory Integration Tests
# ============================================================================

def test_optimus_orchestrator_memory_actions(tmp_path):
    from actions.optimus_orchestrator import optimus_orchestrator

    # 1. Store memory via action tool
    store_out = optimus_orchestrator({
        "action": "memory_store",
        "key": "api_endpoint",
        "content": "https://api.optimus.internal/v1",
        "category": "SEMANTIC",
        "tags": "api,networking",
    })
    assert "status=done" in store_out
    assert "api_endpoint" in store_out

    # 2. Search memory via action tool
    search_out = optimus_orchestrator({
        "action": "memory_search",
        "query": "api endpoint",
        "category": "SEMANTIC",
    })
    assert "status=done" in search_out
    assert "api_endpoint" in search_out
    assert "https://api.optimus.internal/v1" in search_out
