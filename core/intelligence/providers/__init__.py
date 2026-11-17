"""core/intelligence/providers/__init__.py — OPTIMUS Model Providers Package.

Exports:
- ModelProvider: Base provider ABC.
- OllamaProvider: Local Ollama daemon provider.
- OpenAIProvider: OpenAI and OpenAI-compatible provider.
- AnthropicProvider: Anthropic Claude messages provider.
- LocalCustomProvider: Embedded custom local provider and Mark-LVII bridge.
"""

from __future__ import annotations

from core.intelligence.providers.anthropic import AnthropicProvider
from core.intelligence.providers.base import ModelProvider
from core.intelligence.providers.local_custom import LocalCustomProvider
from core.intelligence.providers.ollama import OllamaProvider
from core.intelligence.providers.openai import OpenAIProvider

__all__ = [
    "AnthropicProvider",
    "LocalCustomProvider",
    "ModelProvider",
    "OllamaProvider",
    "OpenAIProvider",
]
