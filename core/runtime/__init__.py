"""core/runtime — Hardware-Adaptive Runtime & Resource Governance for OPTIMUS."""

from core.runtime.hardware import (
    AccelerationBackend,
    CpuInfo,
    DiskInfo,
    GpuInfo,
    GpuVendor,
    HardwareDetector,
    HardwareReport,
    MemoryInfo,
)
from core.runtime.lifecycle import (
    ActiveModelInstance,
    LocalBackendType,
    LocalModelLifecycleManager,
    LocalModelSpec,
    LocalModelStatus,
)
from core.runtime.model_sizing import (
    estimate_model_hardware_requirements,
    get_optimal_cpu_threads,
)
from core.runtime.profiles import (
    CpuOnlyProfile,
    GpuAcceleratedProfile,
    HardwareProfile,
    HardwareProfileType,
    HybridProfile,
    evaluate_hardware_profile,
)
from core.runtime.resources import (
    AdaptiveResourceManager,
    MemoryPressureLevel,
    ResourceBudget,
)

__all__ = [
    "AccelerationBackend",
    "ActiveModelInstance",
    "AdaptiveResourceManager",
    "CpuInfo",
    "CpuOnlyProfile",
    "DiskInfo",
    "estimate_model_hardware_requirements",
    "get_optimal_cpu_threads",
    "GpuAcceleratedProfile",
    "GpuInfo",
    "GpuVendor",
    "HardwareDetector",
    "HardwareProfile",
    "HardwareProfileType",
    "HardwareReport",
    "HybridProfile",
    "LocalBackendType",
    "LocalModelLifecycleManager",
    "LocalModelSpec",
    "LocalModelStatus",
    "MemoryInfo",
    "MemoryPressureLevel",
    "ResourceBudget",
    "evaluate_hardware_profile",
]
