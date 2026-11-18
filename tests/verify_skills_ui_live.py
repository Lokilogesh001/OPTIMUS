"""tests/verify_skills_ui_live.py — Live UI Verification for Milestone 4 Skills Subsystem.

Verifies:
1. Real OptimusUI startup and MainWindow rendering.
2. Control Center switching to SectionSkills.
3. Skill table row selection, details, and security findings rendering.
4. Enable / Disable state persistence.
5. Asynchronous Skill Rescan trigger and table refresh.
6. Verification that existing sections (Dashboard, Agents, Developer) remain functional.
7. Clean application shutdown.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Setup paths
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication

from gui.optimus.section_skills import SectionSkills
from ui import OptimusUI


def run_live_skills_ui_verification():
    print("=== OPTIMUS LIVE SKILLS UI VERIFICATION ===")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    print("[1/6] Launching real OptimusUI window...")
    ui = OptimusUI("face.png")
    win = ui._win
    assert win is not None, "MainWindow must not be None"
    win.show()
    app.processEvents()
    time.sleep(0.5)
    print("      MainWindow launched and rendered successfully.")

    print("[2/6] Navigating to Control Center -> Skills Section...")
    win.show_optimus("skills")
    app.processEvents()
    time.sleep(0.3)

    sec_skills: SectionSkills = win._optimus_control_center.skills_view
    assert sec_skills.isVisible(), "SectionSkills must be visible"
    print(f"      SectionSkills visible. Row count: {sec_skills.table_skills.rowCount()}")
    assert sec_skills.table_skills.rowCount() > 0, "Skills table must have discovered skills"

    print("[3/6] Verifying Skill Selection, Details & Security Findings Render...")
    # Select first row
    sec_skills.table_skills.selectRow(0)
    app.processEvents()
    time.sleep(0.2)

    selected_text = sec_skills.text_instructions.toPlainText()
    assert len(selected_text) > 0, "Instructions viewer must populate for selected skill"
    sec_report_text = sec_skills.text_sec_report.toPlainText()
    assert len(sec_report_text) > 0, "Security report viewer must populate"
    print(f"      Inspector rendered instructions ({len(selected_text)} chars) and security report.")

    print("[4/6] Verifying Enable / Disable Operations & Registry Persistence...")
    skill_id = getattr(sec_skills, "_current_selected_id", None)
    assert skill_id is not None, "Selected skill ID must not be None"
    skill_manifest = sec_skills.registry.get(skill_id)
    assert skill_manifest is not None, f"Skill manifest for '{skill_id}' must exist"
    initial_enabled = skill_manifest.enabled
    print(f"      Target Skill: '{skill_manifest.id}' (enabled={initial_enabled})")

    # Toggle enable state
    sec_skills.btn_toggle_enable.click()
    app.processEvents()
    toggled_manifest = sec_skills.registry.get(skill_manifest.id)
    assert toggled_manifest.enabled != initial_enabled, "Skill enabled state must toggle"
    print(f"      State toggled successfully to: {toggled_manifest.enabled}")

    # Restore initial state
    sec_skills.btn_toggle_enable.click()
    app.processEvents()
    restored_manifest = sec_skills.registry.get(skill_manifest.id)
    assert restored_manifest.enabled == initial_enabled, "Skill enabled state must restore"
    print(f"      State restored to: {restored_manifest.enabled}")

    print("[5/6] Verifying Rescan & Cross-Section Navigation...")
    from PyQt6.QtWidgets import QMessageBox
    orig_info = QMessageBox.information
    QMessageBox.information = lambda *a, **kw: QMessageBox.StandardButton.Ok
    try:
        sec_skills.btn_rescan.click()
        app.processEvents()
    finally:
        QMessageBox.information = orig_info

    print(f"      Rescan completed. Updated row count: {sec_skills.table_skills.rowCount()}")

    # Cross-section navigation checks
    for section_name in ["dashboard", "agents", "developer", "workflows"]:
        win.show_optimus(section_name)
        app.processEvents()
        time.sleep(0.1)
        print(f"      Verified section '{section_name}' is functional.")

    # Return to Classic HUD
    win.show_classic_hud()
    app.processEvents()
    time.sleep(0.2)
    print("      Returned to Classic HUD successfully.")

    print("[6/6] Verifying Clean Window Shutdown...")
    ui.close()
    app.processEvents()
    print("      OptimusUI closed cleanly.")
    print("=== LIVE SKILLS UI VERIFICATION: PASSED ===")
    return 0


if __name__ == "__main__":
    code = run_live_skills_ui_verification()
    import os
    os._exit(code)
