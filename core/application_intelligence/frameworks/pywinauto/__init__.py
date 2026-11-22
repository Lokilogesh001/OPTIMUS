"""core/application_intelligence/frameworks/pywinauto — pywinauto UIA & Win32 Integration."""

from core.application_intelligence.frameworks.pywinauto.control_patterns import (
    PywinautoControlPatterns,
    UIAControlPatterns,
)
from core.application_intelligence.frameworks.pywinauto.pywinauto_backend import (
    PywinautoExecutionBackend,
)

__all__ = ["PywinautoExecutionBackend", "UIAControlPatterns", "PywinautoControlPatterns"]
