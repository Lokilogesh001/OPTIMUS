"""End-to-end UI rendering and interaction test for OPTIMUS Personal Life Intelligence Connected Accounts."""

import os
import sys

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton

from core.personal_intelligence.storage import PersonalStore
from gui.optimus.control_center import OptimusControlCenterWidget
from gui.optimus.section_personal import GoogleCredentialsDialog, SectionPersonal

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication instance for headless Qt testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], "-platform", "offscreen"])
    yield app


def test_section_personal_accounts_view_visibility(qapp, tmp_path):
    """Verify that Connected Accounts toolbar and table are visible with valid geometry inside HoloPanel."""
    store = PersonalStore(root_dir=tmp_path / "personal_test")
    sec = SectionPersonal()
    sec.store = store
    sec.resize(1200, 800)
    sec.show()

    # Switch to Connected Accounts (sub-tab index 4)
    sec._switch_sub_view(4)
    qapp.processEvents()

    assert sec.stack.currentIndex() == 4

    # Verify buttons are visible and properly sized
    assert sec.btn_oauth_connect_gmail.isVisible()
    assert sec.btn_oauth_connect_gmail.geometry().height() >= 24
    assert sec.btn_oauth_connect_gcal.isVisible()
    assert sec.btn_oauth_connect_gcal.geometry().height() >= 24
    assert sec.btn_oauth_help.isVisible()
    assert sec.lbl_accounts_status.isVisible()

    # Verify table is visible and has proper layout inside panel.body
    assert sec.tbl_accounts.isVisible()
    assert sec.tbl_accounts.geometry().width() > 100
    assert sec.tbl_accounts.geometry().height() > 100
    assert sec.tbl_accounts.rowCount() >= 4

    # Verify Gmail catalog row is present with an interactive connect button
    found_gmail = False
    for r in range(sec.tbl_accounts.rowCount()):
        item = sec.tbl_accounts.item(r, 0)
        if item and "Gmail" in item.text():
            found_gmail = True
            act_w = sec.tbl_accounts.cellWidget(r, 5)
            assert act_w is not None
            btn = act_w.findChild(QPushButton)
            assert btn is not None
            assert "GMAIL" in btn.text().upper()

    assert found_gmail, "Google Gmail catalog row was not rendered"


def test_google_credentials_dialog(qapp, monkeypatch):
    """Verify GoogleCredentialsDialog accepts and persists credentials."""
    saved_creds = {}

    def mock_store(key, val):
        saved_creds[key] = val
        return True

    monkeypatch.setattr("gui.optimus.section_personal.store_credential", mock_store)

    dlg = GoogleCredentialsDialog()
    dlg.txt_client_id.setText("test-client-id-123.apps.googleusercontent.com")
    dlg.txt_client_secret.setText("test-client-secret-xyz")

    dlg._save_and_accept()

    assert saved_creds.get("google_client_id") == "test-client-id-123.apps.googleusercontent.com"
    assert saved_creds.get("google_client_secret") == "test-client-secret-xyz"


def test_control_center_personal_navigation(qapp):
    """Verify navigating to PERSONAL in OptimusControlCenterWidget renders without layout errors."""
    cc = OptimusControlCenterWidget()
    cc.resize(1300, 850)
    cc.show()

    # Navigate to PERSONAL (Tab 15)
    cc._on_tab_selected(15, "PERSONAL")
    qapp.processEvents()

    assert cc.stack.currentIndex() == 15
    p_view = cc.personal_view

    # Switch to sub-tab 4 (Connected Accounts)
    p_view._switch_sub_view(4)
    qapp.processEvents()

    assert p_view.stack.currentIndex() == 4
    assert p_view.btn_oauth_connect_gmail.isVisible()
    assert p_view.tbl_accounts.isVisible()
    assert p_view.tbl_accounts.rowCount() >= 4
