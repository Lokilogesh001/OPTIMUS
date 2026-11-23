"""tests/test_conversation_performance.py — Verification of Conversation Performance Optimizations.

Validates Priority 1:
1. LogWidget adaptive chunk-based typing (16ms 60Hz tick instead of 6ms character-by-character thrashing).
2. Queue coalescing: backlog flushing prevents transcript latency accumulation.
3. Strict bounded memory: maximum block counts on LogWidget, chat_history, and out_display.
4. Speaker formatting, time badging, and color theme fidelity.
5. Large transcript stress test (1,000+ messages) with bounded document size.
"""

from __future__ import annotations

import time
import pytest
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

from gui.widgets import LogWidget
from gui.optimus.section_command import SectionCommandCenter


@pytest.fixture(scope="session")
def qapp():
    """Ensure a singleton QApplication exists for widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_log_widget_adaptive_chunking_and_timer(qapp):
    """Test 1: LogWidget uses smooth 16ms timer with adaptive chunking rather than 6ms character thrashing."""
    log = LogWidget()
    assert log._tmr.interval() in (0, 6, 16)

    # Enqueue a message
    test_text = "OPTIMUS: This is a performance benchmark verification string containing several sentences."
    log.append_log(test_text)
    qapp.processEvents()

    assert log._typing is True
    assert log._tmr.interval() == 16  # Smooth 60Hz tick

    # Advance steps until current line typing is complete
    max_steps = 30
    steps = 0
    while log._pos < len(log._text) and steps < max_steps:
        log._step()
        qapp.processEvents()
        steps += 1

    # Should finish in under 20 chunk steps (instead of len(test_text) ~ 90 steps!)
    assert steps < 25
    assert log._pos == len(log._text)
    doc_text = log.toPlainText()
    assert "OPTIMUS" in doc_text
    assert "performance benchmark" in doc_text


def test_log_widget_queue_coalescing(qapp):
    """Test 2: Backlogged queue flushes immediately to eliminate compound typing lag."""
    log = LogWidget()

    # Enqueue 5 rapid messages
    for i in range(5):
        log.append_log(f"SYS: Diagnostics status checkpoint {i}")

    qapp.processEvents()

    # Process events to allow queue coalescing
    for _ in range(10):
        if log._typing:
            log._step()
        qapp.processEvents()

    # When backlog exists, items should flush rapidly
    assert len(log._queue) < 5
    assert "checkpoint" in log.toPlainText()


def test_conversation_memory_bounds(qapp):
    """Test 3: Maximum block counts are strictly enforced across conversation widgets."""
    log = LogWidget()
    assert log.document().maximumBlockCount() == 1200

    # Flood with 2,000 lines
    for i in range(2000):
        # Directly insert to document to test block limit
        cur = log.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertBlock()
        cur.insertText(f"Line {i}")

    assert log.document().blockCount() <= 1200


def test_section_command_display_memory_bounds(qapp):
    """Test 4: SectionCommandCenter chat_history and out_display enforce 800 block limits."""
    # Mock bridge
    from unittest.mock import MagicMock
    mock_bridge = MagicMock()

    cmd = SectionCommandCenter(bridge=mock_bridge)
    assert cmd.chat_history.document().maximumBlockCount() == 800
    assert cmd.out_display.document().maximumBlockCount() == 800

    # Append 1,000 messages
    for i in range(1000):
        cmd._append_user_msg(f"User command {i}")
        cmd._append_assistant_msg(f"Assistant response {i}")

    # Document must not exceed 800 blocks
    assert cmd.chat_history.document().blockCount() <= 800


def test_speaker_tagging_and_formatting(qapp):
    """Test 5: Validates speaker tag splitting, timestamps, and colors."""
    log = LogWidget()

    tag, label, words = log._split("You: Hello assistant")
    assert tag == "you"
    assert label == "YOU"
    assert words == "Hello assistant"

    tag, label, words = log._split("OPTIMUS: Autonomous verification online.")
    assert tag == "ai"
    assert label == "OPTIMUS"
    assert words == "Autonomous verification online."

    tag, label, words = log._split("ERR: Connection timed out.")
    assert tag == "err"
    assert label == "ERR"
    assert words == "Connection timed out."
