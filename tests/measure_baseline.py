"""tests/measure_baseline.py — Phase A Baseline Performance Capture."""

import os
import sys
import time
from pathlib import Path

import psutil

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

proc = psutil.Process(os.getpid())

# 1. Cold Startup Measurement
t0 = time.perf_counter()
mem_init = proc.memory_info().rss / (1024 * 1024)

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)
from ui import OptimusUI

ui = OptimusUI("face.png")
t_cold_startup = time.perf_counter() - t0
mem_loaded = proc.memory_info().rss / (1024 * 1024)

time.sleep(1.0)
app.processEvents()

mem_settled = proc.memory_info().rss / (1024 * 1024)
mem_vms = proc.memory_info().vms / (1024 * 1024)
threads = len(proc.threads())
cpu_idle = proc.cpu_percent(interval=0.5)

# Child processes memory
tree_rss = sum(p.memory_info().rss for p in proc.children(recursive=True)) / (1024 * 1024) + mem_settled

# 2. Warm Startup Simulation
t1 = time.perf_counter()
ui2 = OptimusUI("face.png")
t_warm_startup = time.perf_counter() - t1
ui2._win.close()

# 3. Context Construction Latency
from core.memory.manager import ensure as ensure_memory_mgr

mem_mgr = ensure_memory_mgr()
ctx_bus = mem_mgr.context_bus
ctx_bus.isolate_task("bench_task_01")
ctx_bus.publish_subtask_output(
    "bench_task_01",
    "st_1",
    "code_agent",
    "out_key",
    "def foo(): return 42",
    is_verified=True,
)

t_ctx_0 = time.perf_counter()
for _ in range(50):
    _env = ctx_bus.create_envelope("bench_task_01", "st_2", "test_agent", "Goal test", "Title", "Desc")
t_ctx_avg_ms = ((time.perf_counter() - t_ctx_0) / 50.0) * 1000.0

# 4. Agent Planning Latency
from core.orchestrator.orchestrator import CentralOrchestrator

orch = CentralOrchestrator(
    brain_fn=lambda s, t, sc: {
        "text": '[{"agent": "code_intelligence", "title": "Inspect code", "description": "Inspect syntax"}]',
        "calls": [],
    }
)
t_plan_0 = time.perf_counter()
for _ in range(20):
    _p = orch.plan("Inspect syntax and refactor code.py")
t_plan_avg_ms = ((time.perf_counter() - t_plan_0) / 20.0) * 1000.0

# 5. Task Execution Latency (Math task)
t_task_0 = time.perf_counter()
_task_res = orch.execute_task("Calculate 45 * 2 + 10")
t_task_ms = (time.perf_counter() - t_task_0) * 1000.0

# 6. Model On-Demand Loading & Unloading Latency
from core.runtime.lifecycle import LocalBackendType, LocalModelLifecycleManager, LocalModelSpec

mgr = LocalModelLifecycleManager.instance()
spec = LocalModelSpec("qwen_bench", "Qwen Bench", LocalBackendType.OLLAMA_CPU, 512.0, 0.0, 4096, "Q4", True)
t_m0 = time.perf_counter()
mgr.load_model_on_demand(spec)
t_load_ms = (time.perf_counter() - t_m0) * 1000.0

t_m1 = time.perf_counter()
mgr.unload_model("qwen_bench", force=True)
t_unload_ms = (time.perf_counter() - t_m1) * 1000.0

print("=== PHASE A PERFORMANCE BASELINE REPORT ===")
print(f"COLD_STARTUP_S: {t_cold_startup:.3f}")
print(f"WARM_STARTUP_S: {t_warm_startup:.3f}")
print(f"INITIAL_RAM_RSS_MB: {mem_init:.2f}")
print(f"LOADED_RAM_RSS_MB: {mem_loaded:.2f}")
print(f"SETTLED_RAM_RSS_MB: {mem_settled:.2f}")
print(f"SETTLED_RAM_VMS_MB: {mem_vms:.2f}")
print(f"TOTAL_PROCESS_TREE_RAM_MB: {tree_rss:.2f}")
print(f"IDLE_CPU_PCT: {cpu_idle:.2f}")
print(f"ACTIVE_THREAD_COUNT: {threads}")
print(f"CONTEXT_CONSTRUCTION_LATENCY_MS: {t_ctx_avg_ms:.3f}")
print(f"ORCHESTRATOR_PLAN_LATENCY_MS: {t_plan_avg_ms:.3f}")
print(f"TASK_EXECUTION_LATENCY_MS: {t_task_ms:.3f}")
print(f"MODEL_LOAD_LATENCY_MS: {t_load_ms:.3f}")
print(f"MODEL_UNLOAD_LATENCY_MS: {t_unload_ms:.3f}")

ui._win.close()
