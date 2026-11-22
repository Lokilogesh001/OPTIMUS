"""core/application_intelligence/frameworks — Advanced Execution Framework Integrations (M18).

Provides modular, robust execution backends under OPTIMUS application intelligence:
- Microsoft UFO² (WinCOM & AppAgent action schemas)
- Microsoft Playwright (Deterministic DOM, navigation, and downloads)
- Stagehand (Natural-language observe, act, and extract)
- pywinauto (Deep UIA pattern invocation and menu traversal)
- Microsoft OmniParser (Optional, lazy-loaded visual grounding)
"""

from core.application_intelligence.frameworks.base import (
    BaseExecutionProvider,
    ExecutionRequest,
    ExecutionResponse,
    ProviderHealthStatus,
    com_apartment,
)
from core.application_intelligence.frameworks.browser import (
    PlaywrightBrowserBackend,
    StagehandBrowserIntelligence,
)
from core.application_intelligence.frameworks.omniparser import (
    OmniParserBenchmark,
    OmniParserVisualGrounding,
)
from core.application_intelligence.frameworks.pywinauto import (
    PywinautoExecutionBackend,
    UIAControlPatterns,
)
from core.application_intelligence.frameworks.registry import ProviderRegistry
from core.application_intelligence.frameworks.ufo import (
    UFOAppAgentSchemas,
    UFODesktopBackend,
    WinCOMBridge,
)

__all__ = [
    "BaseExecutionProvider",
    "ExecutionRequest",
    "ExecutionResponse",
    "ProviderHealthStatus",
    "com_apartment",
    "ProviderRegistry",
    "UFODesktopBackend",
    "WinCOMBridge",
    "UFOAppAgentSchemas",
    "PywinautoExecutionBackend",
    "UIAControlPatterns",
    "PlaywrightBrowserBackend",
    "StagehandBrowserIntelligence",
    "OmniParserVisualGrounding",
    "OmniParserBenchmark",
]
