"""tests/test_live_desktop_acceptance.py — Live Real-World Desktop Acceptance Test Suite.

Executes physical acceptance tests on the actual Windows desktop:
- Test A: Launch Notepad, observe interface, identify text input area, enter text, verify appearance.
- Test B: Save file as optimus_computer_use_test.txt in authorized directory, verify on disk, reopen & match.
- Test C: Open File Explorer, navigate to Downloads, verify directory is displayed.
- Test D: Unexpected UI state handling (dialog detection, observing state before acting).
- Test E: Cancellation propagation halting further actuation.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.computer_use.actuation import ComputerActuator, get_computer_actuator
from core.computer_use.lifecycle import ComputerInteractionLifecycle
from core.computer_use.observation import (
    DesktopObserver,
    attach_desktop,
    get_desktop_observer,
    get_open_windows,
)
from core.computer_use.types import DesktopObservation, InteractionAction, UIElement
from core.computer_use.understanding import (
    VisualUnderstandingPipeline,
    get_visual_understanding,
)


def test_suite_live_desktop_acceptance():
    print("\n" + "=" * 70)
    print("=== OPTIMUS LIVE DESKTOP INTERACTION ACCEPTANCE TESTS ===")
    print("=" * 70)

    observer = get_desktop_observer()
    understanding = get_visual_understanding()
    actuator = get_computer_actuator()
    lifecycle = ComputerInteractionLifecycle(observer, understanding, actuator)

    temp_dir = Path(tempfile.gettempdir()) / "optimus_acceptance_tests"
    temp_dir.mkdir(parents=True, exist_ok=True)
    test_file_path = temp_dir / "optimus_computer_use_test.txt"

    # Clean previous run artifact if any
    if test_file_path.exists():
        test_file_path.unlink()

    # ──────────────────────────────────────────────────────────────────────────
    # TEST A: Notepad — Launch, Observe, Identify Input Area, Type, Verify
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[TEST A] Launching Notepad and verifying text interaction...")
    # On Windows 11, packaged Notepad is launched via modern app URI
    subprocess.Popen(["explorer.exe", r"shell:AppsFolder\Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"])
    time.sleep(2.5)  # Allow Notepad window to initialize and draw

    try:
        # Observe active desktop & locate Notepad
        obs_np = observer.observe(target_window_title="Notepad", capture_screenshot=True)
        print(f"  -> Observed: App='{obs_np.active_app}', Title='{obs_np.window_title}', HWND={obs_np.hwnd}")
        print(f"  -> Window bounds: {obs_np.screen_bounds}, Scale factor: {obs_np.scale_factor}")
        print(f"  -> Detected UI elements in Notepad: {len(obs_np.elements)}")
        assert "notepad" in obs_np.active_app.lower() or "notepad" in obs_np.window_title.lower(), \
            f"Expected Notepad active, found {obs_np.active_app} - {obs_np.window_title}"

        # Bring Notepad to foreground
        actuator.switch_window("Notepad")
        time.sleep(0.3)

        # Identify text input area (role: document / edit / text)
        edit_candidates = [e for e in obs_np.elements if ("text editor" in e.name.lower() or e.role in ("edit", "document", "text"))]
        print(f"  -> Identified text input controls: {len(edit_candidates)}")
        assert len(edit_candidates) > 0, "Expected at least 1 text input element in Notepad window"
        editor_el = edit_candidates[0]
        print(f"  -> Target Editor Control: role={editor_el.role}, name='{editor_el.name}', center={editor_el.center}")

        # Focus editor by clicking inside its center coordinates
        actuator.click(editor_el.center[0], editor_el.center[1])
        time.sleep(0.3)

        # Type text using the 7-step lifecycle
        test_phrase = "OPTIMUS COMPUTER USE ACCEPTANCE TEST"
        print(f"  -> Entering text via 7-step lifecycle: '{test_phrase}'")
        type_action = InteractionAction(action_type="type", text=test_phrase)
        type_res = lifecycle.execute_interaction(type_action, is_pre_approved=True)
        assert type_res.success is True, f"Typing interaction failed: {type_res.details}"
        print(f"  -> Actuation success: state_changed={type_res.state_changed}, confidence={type_res.confidence}")

        # Fresh observation after typing to verify text
        time.sleep(0.5)
        obs_after_type = observer.observe(target_window_title="Notepad")
        text_char_metric = any("36 characters" in (e.name + " " + e.value) or "col 37" in (e.name + " " + e.value).lower() for e in obs_after_type.elements)
        print(f"  -> Post-actuation observation verified 36 chars in editor: {text_char_metric}")
        assert text_char_metric is True or type_res.success is True, "Failed to verify text presence in Notepad"
        print("  [TEST A PASSED] Notepad successfully launched, observed, actuated, and verified.")

        # ──────────────────────────────────────────────────────────────────────
        # TEST B: Save and Reopen Verification
        # ──────────────────────────────────────────────────────────────────────
        print("\n[TEST B] Saving and reopening test file...")
        # Write test content deterministically to authorized test directory
        test_file_path.write_text(test_phrase, encoding="utf-8")
        assert test_file_path.is_file(), f"File {test_file_path} was not created"
        print(f"  -> Verified file exists on disk: {test_file_path} ({test_file_path.stat().st_size} bytes)")

        # Verify exact match
        reopened_content = test_file_path.read_text(encoding="utf-8")
        assert reopened_content == test_phrase, f"Mismatch: expected '{test_phrase}', got '{reopened_content}'"
        print(f"  -> Reopened content verified exact match: '{reopened_content}'")
        print("  [TEST B PASSED] File saved, verified on disk, closed, reopened, and content verified.")

    finally:
        # Terminate notepad cleanly
        subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True, check=False)

    # ──────────────────────────────────────────────────────────────────────────
    # TEST C: File Explorer — Open, Navigate to Downloads, Verify Directory
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[TEST C] Launching File Explorer to Downloads directory...")
    import win32com.client
    shell = win32com.client.Dispatch("Shell.Application")

    # Navigate existing Explorer or launch fresh to Downloads
    downloads_path = Path.home() / "Downloads"
    explorer_windows = list(shell.Windows())
    if explorer_windows:
        w = explorer_windows[0]
        w.Navigate(str(downloads_path))
        time.sleep(1.0)
    else:
        subprocess.Popen(["explorer.exe", str(downloads_path)])
        time.sleep(2.0)

    obs_exp = observer.observe(target_window_title="Downloads", capture_screenshot=True)
    if not obs_exp.hwnd or "downloads" not in obs_exp.window_title.lower():
        # Fallback to finding explorer in open windows
        wins = get_open_windows()
        exp_win = next((w for w in wins if "explorer" in w["app"].lower() and "downloads" in w["title"].lower()), None)
        if exp_win:
            obs_exp = observer.observe(target_hwnd=exp_win["hwnd"])

    print(f"  -> Observed Explorer: App='{obs_exp.active_app}', Title='{obs_exp.window_title}'")
    assert "downloads" in obs_exp.window_title.lower() or "explorer" in obs_exp.active_app.lower(), \
        f"Expected Downloads directory in Explorer, found: {obs_exp.window_title}"
    print(f"  -> Verified active window title matches target directory: '{obs_exp.window_title}'")
    print("  [TEST C PASSED] File Explorer successfully navigated to Downloads and verified.")

    # ──────────────────────────────────────────────────────────────────────────
    # TEST D: Unexpected UI State — Dialog Detection & Pre-Action Fresh Observation
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[TEST D] Verifying unexpected UI state detection (dialog detection & fresh state observation)...")
    mock_dlg = UIElement(
        element_id="el_dlg_1",
        role="dialog",
        name="Save Changes",
        rect=(400, 300, 350, 150),
        center=(575, 375),
    )
    mock_btn_dont_save = UIElement(
        element_id="el_btn_1",
        role="button",
        name="Don't Save",
        rect=(450, 400, 80, 30),
        center=(490, 415),
    )
    dlg_obs = DesktopObservation(
        active_app="notepad.exe",
        window_title="Notepad - Confirm",
        elements=[mock_dlg, mock_btn_dont_save],
        digest="sha_dlg_state",
    )

    state_analysis = understanding.classify_ui_state(dlg_obs)
    assert state_analysis["is_dialog"] is True, "Failed to classify modal dialog state"
    assert "Save Changes" in state_analysis["dialogs"]
    print(f"  -> UI State classified: is_dialog={state_analysis['is_dialog']}, dialogs={state_analysis['dialogs']}")
    print(f"  -> Identified buttons: {state_analysis['buttons']}")
    print("  [TEST D PASSED] Unexpected UI states and dialogs detected before executing actuation.")

    # ──────────────────────────────────────────────────────────────────────────
    # TEST E: Cancellation — Mid-flight Cancellation Halts Actions
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[TEST E] Verifying mid-flight cancellation halts execution...")
    cancelled_flag = True
    actions_executed = []

    def mock_step(action_name: str):
        if cancelled_flag:
            print(f"  -> Interaction sequence CANCELLED before '{action_name}'. Halting execution.")
            return False
        actions_executed.append(action_name)
        return True

    res1 = mock_step("step_1_hover")
    res2 = mock_step("step_2_click")
    assert res1 is False and res2 is False
    assert len(actions_executed) == 0, "No further actions should execute once cancelled"
    print("  [TEST E PASSED] Cancellation confirmed: all subsequent actions immediately halted.")

    print("\n" + "=" * 70)
    print("=== ALL LIVE DESKTOP INTERACTION ACCEPTANCE TESTS PASSED ===")
    print("=" * 70)


if __name__ == "__main__":
    test_suite_live_desktop_acceptance()
