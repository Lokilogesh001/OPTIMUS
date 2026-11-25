"""gui/optimus/graph_builders.py — Real Backend Data Builders for OPTIMUS Graph Views.

Builds:
1. Integration Graph: OPTIMUS Core -> Agents -> Tools/Capabilities -> Apps & Services -> Model Providers.
2. Memory Graph: MemoryManager entries & ContextBus partitions with explicit direct vs derived edges.
3. Task Execution Graph: Authoritative TaskStore/Orchestrator DAG, dependencies, retries, and verification.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from gui.optimus.graph_widget import GraphEdge, GraphNode


def build_integration_graph(bridge: Any, workspace_adapter: Any = None) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Build Integration Graph from real orchestrator, agents, tools, apps, services, and providers."""
    nodes: list[GraphNode] = [
        GraphNode(
            node_id="core:optimus",
            label="OPTIMUS Core",
            category="Core",
            status="ACTIVE",
            subtitle="Hybrid Orchestrator & PolicyEngine",
            details={"architecture": "Local-First PyQt6 + TaskStore + PolicyEngine"},
        )
    ]
    edges: list[GraphEdge] = []

    # 1. Model Providers
    try:
        intel = bridge.get_intelligence_status() or {}
        active_prov = str(intel.get("active_provider") or "")
        providers = intel.get("providers") or []
        for idx, p in enumerate(providers[:8]):
            p_name = str(p.get("name") or p.get("provider") or f"provider_{idx}")
            healthy = bool(p.get("healthy", p.get("available", False)))
            st = "CONNECTED" if healthy else "UNAVAILABLE"
            pid = f"provider:{p_name}"
            nodes.append(GraphNode(
                node_id=pid,
                label=p_name,
                category="Model Provider",
                status=st,
                subtitle=str(p.get("model") or ("active" if p_name == active_prov else "configured")),
                details=dict(p),
            ))
            edges.append(GraphEdge("core:optimus", pid, "inference", "direct"))
    except Exception:
        pass

    # 2. Agents
    agent_ids: list[str] = []
    try:
        agents = bridge.get_agents_status() or []
        for a in agents[:16]:
            a_name = str(a.get("name") or "agent")
            aid = f"agent:{a_name}"
            agent_ids.append(aid)
            st = "ACTIVE" if a.get("current_task") else "AVAILABLE"
            nodes.append(GraphNode(
                node_id=aid,
                label=a_name,
                category="Agent",
                status=st,
                subtitle=str(a.get("domain") or a.get("role") or ""),
                details=dict(a),
            ))
            edges.append(GraphEdge("core:optimus", aid, "delegates", "direct"))
    except Exception:
        pass

    # 3. Tools & Capabilities
    try:
        from core.orchestrator.tools import ToolRegistry
        reg = ToolRegistry.instance()
        tools = reg.list_tools() if hasattr(reg, "list_tools") else []
        for t in tools[:18]:
            t_name = str(getattr(t, "name", t))
            tier = getattr(getattr(t, "capability_tier", None), "value", "READ_ONLY")
            mut = bool(getattr(t, "is_mutating", False))
            tid = f"tool:{t_name}"
            nodes.append(GraphNode(
                node_id=tid,
                label=t_name,
                category="Tool",
                status="GATED" if mut else "AVAILABLE",
                subtitle=f"tier={tier}",
                details={"name": t_name, "capability_tier": str(tier), "is_mutating": mut},
            ))
            edges.append(GraphEdge("core:optimus", tid, str(tier), "direct"))
    except Exception:
        pass

    # 4. Connected Services (Gmail / Personal Providers)
    try:
        if workspace_adapter is not None:
            gs = workspace_adapter.get_gmail_status() or {}
            g_ok = bool(gs.get("healthy", False))
            gid = "service:gmail"
            nodes.append(GraphNode(
                node_id=gid,
                label="Google Gmail",
                category="Service",
                status="CONNECTED" if g_ok else str(gs.get("auth_status") or "DISCONNECTED"),
                subtitle=str(gs.get("account") or "Workspace MCP"),
                details={k: v for k, v in gs.items() if "token" not in str(k).lower()},
            ))
            edges.append(GraphEdge("core:optimus", gid, "oauth2", "direct"))
    except Exception:
        pass

    try:
        for prov in (bridge.get_supported_personal_providers() or [])[:6]:
            pname = str(prov.get("provider") or prov.get("name") or "")
            if not pname or pname.lower() == "gmail":
                continue
            sid = f"service:{pname}"
            nodes.append(GraphNode(
                node_id=sid,
                label=pname,
                category="Service",
                status=str(prov.get("status") or ("CONNECTED" if prov.get("connected") else "DISCONNECTED")),
                subtitle="Personal Intelligence",
                details=dict(prov),
            ))
            edges.append(GraphEdge("core:optimus", sid, "provider", "direct"))
    except Exception:
        pass

    # 5. Connected / Discovered Applications (bounded for responsiveness)
    try:
        from core.application_intelligence.registry import ApplicationRegistry
        apps = ApplicationRegistry.instance().list_all_apps(auto_refresh=False)
        for app in apps[:12]:
            app_name = str(getattr(app, "name", getattr(app, "app_id", "app")))
            apid = f"app:{app_name}"
            running = bool(getattr(app, "is_running", False))
            nodes.append(GraphNode(
                node_id=apid,
                label=app_name,
                category="Application",
                status="ACTIVE" if running else "AVAILABLE",
                subtitle=str(getattr(app, "category", "desktop")),
                details={
                    "app_id": getattr(app, "app_id", app_name),
                    "is_running": running,
                    "executable": getattr(app, "executable_path", ""),
                },
            ))
            edges.append(GraphEdge("core:optimus", apid, "adapter", "direct"))
    except Exception:
        pass

    return nodes, edges


def build_memory_graph(bridge: Any) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Build Memory Graph from real MemoryManager entries and ContextBus without mutating memory."""
    mem_mgr = getattr(getattr(bridge, "orchestrator", None), "memory_manager", None)
    if mem_mgr is None:
        return [], []

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    entries = []
    try:
        entries = mem_mgr.store.list_all(limit=60)
    except Exception:
        entries = []

    bus_tasks = {}
    try:
        bus_tasks = dict(getattr(mem_mgr.context_bus, "_task_working_context", {}) or {})
    except Exception:
        bus_tasks = {}

    if not entries and not bus_tasks:
        return [], []

    entry_tokens: dict[str, set[str]] = {}
    source_nodes: set[str] = set()
    task_nodes: set[str] = set()

    for e in entries:
        eid = f"mem:{e.entry_id}"
        cat = e.category.value if hasattr(e.category, "value") else str(e.category)
        ts = getattr(e, "updated_at", getattr(e, "created_at", 0.0)) or 0.0
        ts_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "unknown"
        src = str(getattr(e, "source", "") or "memory_store")
        task_id = str((getattr(e, "metadata", None) or {}).get("task_id") or "")
        content = str(getattr(e, "content", "") or "")

        nodes.append(GraphNode(
            node_id=eid,
            label=str(e.key or e.entry_id[:12]),
            category=f"Memory ({cat})",
            status="STORED",
            subtitle=f"src={src}",
            details={
                "entry_id": e.entry_id,
                "category": cat,
                "source": src,
                "timestamp": ts_str,
                "task_id": task_id or "none",
                "content": content[:800],
            },
        ))

        src_id = f"source:{src}"
        if src_id not in source_nodes:
            source_nodes.add(src_id)
            nodes.append(GraphNode(
                node_id=src_id,
                label=src,
                category="Source",
                status="DIRECT",
                subtitle="Memory origin",
                details={"source": src, "relationship": "direct"},
            ))
        edges.append(GraphEdge(src_id, eid, "wrote (direct)", "direct"))

        if task_id:
            tid = f"taskmem:{task_id}"
            if tid not in task_nodes:
                task_nodes.add(tid)
                nodes.append(GraphNode(
                    node_id=tid,
                    label=f"Task {task_id[:12]}",
                    category="Episodic Task",
                    status="DIRECT",
                    subtitle="Task memory link",
                    details={"task_id": task_id, "relationship": "direct"},
                ))
            edges.append(GraphEdge(tid, eid, "task_id (direct)", "direct"))

        toks = {w for w in re.findall(r"[a-z0-9]{4,}", f"{e.key} {content[:120]}".lower())}
        entry_tokens[eid] = toks

    # ContextBus active task partitions (direct)
    for t_id, t_data in list(bus_tasks.items())[:12]:
        tid = f"taskmem:{t_id}"
        if tid not in task_nodes:
            task_nodes.add(tid)
            nodes.append(GraphNode(
                node_id=tid,
                label=f"Bus {t_id[:12]}",
                category="Context Bus",
                status="ACTIVE",
                subtitle=f"{len(t_data.get('subtask_outputs', {}))} outputs",
                details={"task_id": t_id, "deliverables": len(t_data.get("deliverables", []))},
            ))

    # Derived similarity edges (in-memory only, clearly marked as derived)
    eids = list(entry_tokens.keys())
    derived_count = 0
    for i in range(len(eids)):
        for j in range(i + 1, len(eids)):
            if derived_count >= 25:
                break
            shared = entry_tokens[eids[i]] & entry_tokens[eids[j]]
            if len(shared) >= 2:
                edges.append(GraphEdge(eids[i], eids[j], f"derived ({', '.join(sorted(shared)[:2])})", "derived"))
                derived_count += 1

    return nodes, edges


def build_task_execution_graph(bridge: Any, task_id: str | None = None) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Build read-only Task Execution Graph from authoritative TaskStore/Orchestrator state."""
    orch = getattr(bridge, "orchestrator", None)
    task_store = getattr(orch, "task_store", None) if orch else None
    if task_store is None:
        return [], []

    rec = task_store.get_task(task_id) if task_id else None
    if rec is None:
        active_id = getattr(bridge, "_active_task_id", None)
        if active_id:
            rec = task_store.get_task(active_id)
    if rec is None:
        recent = task_store.list_tasks(limit=1)
        rec = recent[0] if recent else None
    if rec is None:
        return [], []

    status_val = rec.status.value if hasattr(rec.status, "value") else str(rec.status)
    nodes: list[GraphNode] = [
        GraphNode(
            node_id="orch:central",
            label="CentralOrchestrator",
            category="Orchestrator",
            status="ACTIVE",
            subtitle=f"stage={rec.current_stage}",
            details={"task_id": rec.task_id, "goal": rec.goal},
            x=240.0,
            y=10.0,
        ),
        GraphNode(
            node_id=f"task:{rec.task_id}",
            label=rec.goal[:28] or rec.task_id,
            category="Task",
            status=status_val,
            subtitle=f"retries={rec.retry_count}/{rec.max_retries}",
            details={
                "task_id": rec.task_id,
                "goal": rec.goal,
                "status": status_val,
                "current_stage": rec.current_stage,
                "retry_count": rec.retry_count,
                "max_retries": rec.max_retries,
                "duration_seconds": rec.duration_seconds,
                "error": rec.error or "none",
                "result": (rec.result or "")[:600],
            },
            x=240.0,
            y=105.0,
        ),
    ]
    edges: list[GraphEdge] = [GraphEdge("orch:central", f"task:{rec.task_id}", "dispatches", "direct")]

    if rec.retry_count > 0 or status_val == "RECOVERING":
        r_nid = f"retry:{rec.task_id}"
        nodes.append(GraphNode(
            node_id=r_nid,
            label=f"Recovery ({rec.retry_count}/{rec.max_retries})",
            category="Recovery",
            status="RECOVERING" if status_val == "RECOVERING" else "COMPLETED",
            subtitle="Bounded retry path",
            details={"retry_count": rec.retry_count, "max_retries": rec.max_retries},
            x=470.0,
            y=105.0,
        ))
        edges.append(GraphEdge(f"task:{rec.task_id}", r_nid, "retry_path", "retry"))

    subtasks = list(getattr(getattr(rec, "plan", None), "subtasks", None) or [])
    agents_added: set[str] = set()
    ev_by_sub = {
        ev.get("subtask_id"): ev
        for ev in (getattr(rec, "execution_evidence", None) or [])
        if isinstance(ev, dict) and ev.get("subtask_id")
    }

    for idx, st in enumerate(subtasks):
        st_status = st.status.value if hasattr(st.status, "value") else str(st.status)
        st_nid = f"subtask:{st.subtask_id}"
        x_pos = idx * 210.0
        nodes.append(GraphNode(
            node_id=st_nid,
            label=st.title or st.subtask_id,
            category="Subtask",
            status=st_status,
            subtitle=f"agent={st.assigned_agent}",
            details={
                "subtask_id": st.subtask_id,
                "assigned_agent": st.assigned_agent,
                "status": st_status,
                "allowed_tools": st.allowed_tools,
                "output_summary": (st.output_summary or "")[:400],
                "verification_verdict": st.verification_verdict or "pending",
                "error": st.error or "none",
            },
            x=x_pos,
            y=215.0,
        ))
        edges.append(GraphEdge(f"task:{rec.task_id}", st_nid, f"step {st.index}", "direct"))

        # Dependency edges if any
        for dep in getattr(st, "depends_on", []) or []:
            edges.append(GraphEdge(f"subtask:{dep}", st_nid, "depends_on", "direct"))

        # Agent node
        ag_name = str(st.assigned_agent or "general")
        ag_nid = f"agent:{ag_name}"
        if ag_nid not in agents_added:
            agents_added.add(ag_nid)
            nodes.append(GraphNode(
                node_id=ag_nid,
                label=ag_name,
                category="Agent",
                status="ACTIVE" if st_status in ("RUNNING", "DELEGATED") else st_status,
                subtitle="Delegated worker",
                details={"agent": ag_name},
                x=x_pos,
                y=320.0,
            ))
        edges.append(GraphEdge(st_nid, ag_nid, "assigned_to", "direct"))

        # Verification stage node
        ev = ev_by_sub.get(st.subtask_id)
        if ev or st.verification_verdict or st_status in ("VERIFYING", "COMPLETED", "FAILED"):
            v_ok = bool(ev.get("verified")) if ev else (st_status == "COMPLETED")
            v_nid = f"verify:{st.subtask_id}"
            nodes.append(GraphNode(
                node_id=v_nid,
                label=f"Verify {st.index}",
                category="Verification",
                status="VERIFIED" if v_ok else ("VERIFYING" if st_status == "VERIFYING" else "FAILED"),
                subtitle=str((ev or {}).get("verdict") or st.verification_verdict or st_status),
                details=dict(ev or {"verdict": st.verification_verdict}),
                x=x_pos,
                y=420.0,
            ))
            edges.append(GraphEdge(st_nid, v_nid, "verifies", "direct"))

    return nodes, edges
