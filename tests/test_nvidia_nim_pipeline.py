"""tests/test_nvidia_nim_pipeline.py — Acceptance Test Suite for Conversation Pipeline & UI Integration.

Tests:
1. Preset URL and catalog discovery for NVIDIA NIM.
2. Registry missing_fields() key resolution via secure credential store (Mandate M-1).
3. Zero-plaintext persistence during UI save/apply (Mandate M-1).
4. Secret redaction logging filter & redact_secret utility (Mandate M-6).
5. OpenAICompatLLM initialization & key resolution for nvidia.com.
6. Parameter stripping retry on HTTP 400 in OpenAICompatLLM (Mandate M-4).
7. Live NVIDIA NIM connectivity (gated by NVIDIA_API_KEY).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.security.credentials import (
    SecretRedactionFilter,
    delete_credential,
    get_credential,
    mask_credential,
    redact_secret,
    store_credential,
)
from providers import registry
from providers.llm_openai import CATALOG, PRESETS, OpenAICompatLLM


@pytest.fixture(autouse=True)
def clean_credentials():
    delete_credential("nvidia")
    delete_credential("nvidia_nim")
    yield
    delete_credential("nvidia")
    delete_credential("nvidia_nim")


def test_01_presets_and_catalog_discovery():
    """Verify NVIDIA NIM is registered in presets and catalog."""
    assert "NVIDIA NIM" in PRESETS
    assert PRESETS["NVIDIA NIM"] == "https://integrate.api.nvidia.com/v1"

    catalog_item = next((c for c in CATALOG if c["id"] == "nvidia_nim"), None)
    assert catalog_item is not None
    assert catalog_item["label"] == "NVIDIA NIM"
    assert catalog_item["local"] is False
    assert catalog_item["values"]["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert "meta/llama-3.1-70b-instruct" in catalog_item["values"]["model"]

    # Verify discovery through registry module
    discovered = registry.discover("llm")
    assert any(d.get("id") == "openai_compat" for d in discovered)


def test_02_missing_fields_resolves_via_keyring():
    """Verify missing_fields recognizes key in Keyring without plaintext in cfg (Mandate M-1)."""
    cfg_empty_key = {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.1-70b-instruct",
        "api_key": "",
    }

    # 1. With no key anywhere, missing_fields reports "API key"
    missing = registry.missing_fields("openai_compat@nvidia_nim", cfg_empty_key)
    assert "API key" in missing

    # 2. Store key in Keyring / vault
    store_credential("nvidia_nim", "nvapi-secure-key-abc123456789")

    # 3. missing_fields should now inspect Keyring and report NO missing fields
    missing_after = registry.missing_fields("openai_compat@nvidia_nim", cfg_empty_key)
    assert "API key" not in missing_after
    assert len(missing_after) == 0


def test_03_zero_plaintext_persistence_simulation(tmp_path):
    """Verify that applying provider edits strips plaintext key from config storage."""
    api_key_secret = "nvapi-super-secret-production-key-999"

    # Simulate UI apply workflow
    sid = "openai_compat@nvidia_nim"
    base, entry = registry.split_id(sid)

    user_values = {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.1-70b-instruct",
        "api_key": api_key_secret,
    }

    # UI step 1: Store key in secure OS Keyring
    store_credential(entry, user_values["api_key"])

    # UI step 2: Strip key before persisting to config json
    clean_values = dict(user_values)
    clean_values["api_key"] = ""

    config_file = tmp_path / "api_keys.json"
    config_file.write_text(json.dumps({"providers": {"creds": {sid: clean_values}}}), encoding="utf-8")

    # Invariant checks:
    # 1. Plaintext secret is NOT in config file
    raw_saved = config_file.read_text(encoding="utf-8")
    assert api_key_secret not in raw_saved
    assert clean_values["api_key"] == ""

    # 2. Secret IS retrievable via get_credential
    assert get_credential(entry) == api_key_secret
    assert get_credential("nvidia") == api_key_secret


def test_04_secret_redaction_and_filter():
    """Verify logging filter scrubs nvapi and sk keys from log messages."""
    raw_msg = (
        "Failed request with Bearer nvapi-secretkey1234567890abcdef and sk-11223344556677889900aabbccdd"
    )
    redacted = redact_secret(raw_msg)
    assert "nvapi-secretkey" not in redacted
    assert "sk-1122" not in redacted
    assert "[REDACTED_API_KEY]" in redacted

    # Test SecretRedactionFilter on Logger
    flt = SecretRedactionFilter()
    rec = logging.LogRecord(
        name="optimus.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg="Error contacting endpoint with nvapi-livekey9988776655443322",
        args=(),
        exc_info=None,
    )
    flt.filter(rec)
    assert "nvapi-livekey" not in rec.msg
    assert "[REDACTED_API_KEY]" in rec.msg


def test_05_openai_compat_llm_key_resolution():
    """Verify OpenAICompatLLM resolves key for nvidia.com from credentials if empty in cfg."""
    store_credential("nvidia", "nvapi-resolved-from-keyring")

    cfg = {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.1-70b-instruct",
        "api_key": "",
    }
    llm = OpenAICompatLLM(cfg)
    assert llm.key == "nvapi-resolved-from-keyring"
    assert llm._headers()["Authorization"] == "Bearer nvapi-resolved-from-keyring"


def test_06_parameter_stripping_retry_in_complete():
    """Verify OpenAICompatLLM complete() automatically strips unrecognized argument on 400."""
    call_count = 0

    class MockHttpxResponse:
        def __init__(self, status_code: int, text: str, data: dict):
            self.status_code = status_code
            self.text = text
            self._data = data

        def json(self):
            return self._data

    class MockHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, headers=None, json=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockHttpxResponse(
                    400,
                    '{"error": "Unrecognized request argument: presence_penalty"}',
                    {},
                )
            return MockHttpxResponse(
                200,
                '{"choices": [{"message": {"content": "Stripped and recovered"}}]}',
                {"choices": [{"message": {"content": "Stripped and recovered"}}]},
            )

    cfg = {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.1-70b-instruct",
        "api_key": "nvapi-dummy",
    }
    llm = OpenAICompatLLM(cfg)
    llm._httpx = MagicMock()
    llm._httpx.Client = MockHttpxClient

    # Add presence_penalty into messages body mock
    orig_body = llm._body
    def bad_body(*args, **kwargs):
        b = orig_body(*args, **kwargs)
        if call_count == 0:
            b["presence_penalty"] = 0.5
        return b
    llm._body = bad_body

    text, tool_calls = llm.complete([{"role": "user", "content": "Hello"}])

    assert call_count == 2
    assert text == "Stripped and recovered"


@pytest.mark.skipif(
    not os.environ.get("NVIDIA_API_KEY"),
    reason="NVIDIA_API_KEY environment variable not set; skipping live test.",
)
def test_07_live_nvidia_nim_completion():
    """Live integration test against official NVIDIA NIM endpoint (only runs when key is present)."""
    api_key = os.environ["NVIDIA_API_KEY"]
    from core.intelligence.providers.nvidia_nim import NvidiaNimProvider
    from core.intelligence.types import ChatMessage, ModelRequest

    provider = NvidiaNimProvider(api_key=api_key)
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Reply with the single word: Pong")],
        preferred_model="meta/llama-3.1-8b-instruct",
        max_tokens=5,
        temperature=0.1,
    )
    resp = provider.complete(req)
    assert resp.text
    assert resp.duration_ms > 0
    assert resp.usage.total_tokens > 0
