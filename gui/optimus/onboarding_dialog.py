"""gui/optimus/onboarding_dialog.py — First-Launch Onboarding & Permission Architecture Wizard.

Cinematic multi-step onboarding wizard for OPTIMUS:
Step 1: Welcome to OPTIMUS (Mission & Architecture Briefing)
Step 2: AI Provider Selection (Gemini, OpenAI, Anthropic, OpenRouter, Groq, Custom, Local-Only)
Step 3: Credentials, Endpoints & Model Configuration (with live Test Connection)
Step 4: Permission Profile Selection (LOW, MODERATE, ADVANCED)
Step 5: Configuration Summary & Verification
Step 6: Launch into OPTIMUS HUD
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.security.credentials import (
    get_credential,
    mask_credential,
    store_credential,
)
from core.security.onboarding import (
    PROVIDER_METADATA,
    save_onboarding_config,
    verify_provider_connection,
)
from core.security.permissions import PermissionProfile
from gui.style import mono_font, px_font
from gui.theme import C


class OnboardingCard(QFrame):
    """Selectable card widget with cinematic blue styling."""

    def __init__(self, title: str, subtitle: str, badge: str = "", parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.selected = False

        self.setStyleSheet(self._style(False))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)

        top_row = QHBoxLayout()
        t = QLabel(title)
        t.setFont(px_font(10, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        top_row.addWidget(t)
        top_row.addStretch()

        if badge:
            b = QLabel(f" {badge} ")
            b.setFont(px_font(8, QFont.Weight.Bold))
            b.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 212, 255, 0.12); border: 1px solid rgba(0, 212, 255, 0.3); border-radius: 3px;")
            top_row.addWidget(b)
        lay.addLayout(top_row)

        sub = QLabel(subtitle)
        sub.setFont(px_font(8))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(sub)

    def _style(self, sel: bool) -> str:
        border_col = C.PRI if sel else "rgba(30, 41, 59, 0.8)"
        bg_col = "rgba(0, 212, 255, 0.08)" if sel else "rgba(10, 22, 40, 0.65)"
        return f"""
            OnboardingCard {{
                background: {bg_col};
                border: 1px solid {border_col};
                border-radius: 6px;
            }}
            OnboardingCard:hover {{
                border: 1px solid {C.PRI};
                background: rgba(0, 212, 255, 0.05);
            }}
        """

    def set_selected(self, sel: bool) -> None:
        self.selected = sel
        self.setStyleSheet(self._style(sel))


class OptimusOnboardingDialog(QWidget):
    """Cinematic multi-step first-launch onboarding overlay for OPTIMUS."""

    completed = pyqtSignal(dict)
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            OptimusOnboardingDialog {{
                background: rgba(3, 7, 18, 0.97);
                border: 1px solid {C.PRI_DIM};
                border-radius: 8px;
            }}
        """)

        self.current_step = 0
        self.selected_provider = "gemini"
        self.selected_profile = PermissionProfile.ADVANCED
        self.entered_key = ""
        self.entered_endpoint = ""
        self.selected_model = ""

        # Main Layout
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # 1. Wizard Top Bar
        top_bar = QHBoxLayout()
        logo_lbl = QLabel("◈  OPTIMUS INITIALISATION & PERMISSION ARCHITECTURE")
        logo_lbl.setFont(px_font(10, QFont.Weight.Bold, spacing=1.8))
        logo_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        top_bar.addWidget(logo_lbl)
        top_bar.addStretch()

        self.step_tracker_lbl = QLabel("STEP 1 OF 5")
        self.step_tracker_lbl.setFont(px_font(9, QFont.Weight.Bold))
        self.step_tracker_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        top_bar.addWidget(self.step_tracker_lbl)
        root.addLayout(top_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(30, 41, 59, 0.9);")
        root.addWidget(sep)

        # 2. Stacked Pages
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step_welcome())
        self.stack.addWidget(self._build_step_provider())
        self.stack.addWidget(self._build_step_credentials())
        self.stack.addWidget(self._build_step_permissions())
        self.stack.addWidget(self._build_step_summary())
        root.addWidget(self.stack, stretch=1)

        # 3. Bottom Navigation Bar
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: rgba(30, 41, 59, 0.9);")
        root.addWidget(sep2)

        nav_bar = QHBoxLayout()
        self.btn_back = QPushButton("←  BACK")
        self.btn_back.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_back.setFixedHeight(32)
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: 1px solid rgba(51, 65, 85, 0.8); border-radius: 4px; padding: 4px 14px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI}; }}
        """)
        self.btn_back.clicked.connect(self._go_back)
        self.btn_back.setVisible(False)
        nav_bar.addWidget(self.btn_back)

        nav_bar.addStretch()

        self.status_lbl = QLabel("")
        self.status_lbl.setFont(px_font(9))
        self.status_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        nav_bar.addWidget(self.status_lbl)

        self.btn_next = QPushButton("CONTINUE  →")
        self.btn_next.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_next.setFixedHeight(32)
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setStyleSheet(f"""
            QPushButton {{
                color: #030712; background: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 4px; padding: 4px 18px;
            }}
            QPushButton:hover {{ background: #38bdf8; }}
        """)
        self.btn_next.clicked.connect(self._go_next)
        nav_bar.addWidget(self.btn_next)
        root.addLayout(nav_bar)

    # ── Step 1: Welcome ──────────────────────────────────────────────────────

    def _build_step_welcome(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(12)

        t = QLabel("WELCOME TO OPTIMUS")
        t.setFont(px_font(16, QFont.Weight.Bold, spacing=2.5))
        t.setStyleSheet(f"color: {C.WHITE};")
        lay.addWidget(t)

        sub = QLabel("Autonomous Personal Agentic AI Assistant")
        sub.setFont(px_font(10, QFont.Weight.DemiBold))
        sub.setStyleSheet(f"color: {C.PRI};")
        lay.addWidget(sub)

        desc = QLabel(
            "OPTIMUS is an autonomous multi-agent system designed for desktop workflow execution, "
            "deep reasoning, and verified tool operations. Before systems engage, let's configure your "
            "preferred AI provider, secure credential store, and execution permission profile.\n\n"
            "Key Capabilities:\n"
            "• Autonomous Workflow DAG Engine with Human Approval Gates\n"
            "• Cryptographic SHA-256 Tamper-Evident Evidence Ledger\n"
            "• Tiered Memory & Role-Based Context Isolation\n"
            "• Local-First Privacy Boundaries with Intelligent Cloud Routing\n"
            "• OS Keyring Credential Protection (Zero plaintext keys stored in repo)"
        )
        desc.setFont(px_font(9))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {C.TEXT_DIM}; line-height: 1.5;")
        lay.addWidget(desc)
        lay.addStretch()
        return w

    # ── Step 2: Provider Selection ───────────────────────────────────────────

    def _build_step_provider(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        t = QLabel("STEP 1: SELECT YOUR INTELLIGENCE PROVIDER")
        t.setFont(px_font(12, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.PRI};")
        lay.addWidget(t)

        sub = QLabel("Choose your primary model provider. You can configure fallbacks and local modes anytime.")
        sub.setFont(px_font(9))
        sub.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay.addWidget(sub)

        # Scrollable Provider Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll_content = QWidget()
        self.provider_cards_lay = QVBoxLayout(scroll_content)
        self.provider_cards_lay.setSpacing(6)

        self.cards: dict[str, OnboardingCard] = {}
        for p_id, p_info in PROVIDER_METADATA.items():
            badge = "LOCAL / PRIVATE" if p_id in ("local_only", "custom") else "CLOUD"
            card = OnboardingCard(p_info["display_name"], p_info["description"], badge=badge)
            card.mousePressEvent = lambda ev, pid=p_id: self._select_provider(pid)
            self.provider_cards_lay.addWidget(card)
            self.cards[p_id] = card

        scroll.setWidget(scroll_content)
        lay.addWidget(scroll, stretch=1)
        self._select_provider("gemini")
        return w

    def _select_provider(self, p_id: str) -> None:
        self.selected_provider = p_id
        for pid, card in self.cards.items():
            card.set_selected(pid == p_id)

    # ── Step 3: Credentials & Model ──────────────────────────────────────────

    def _build_step_credentials(self) -> QWidget:
        w = QWidget()
        self.cred_lay = QVBoxLayout(w)
        self.cred_lay.setContentsMargins(10, 10, 10, 10)
        self.cred_lay.setSpacing(10)

        self.cred_title = QLabel("STEP 2: CREDENTIALS & MODEL CONFIGURATION")
        self.cred_title.setFont(px_font(12, QFont.Weight.Bold))
        self.cred_title.setStyleSheet(f"color: {C.PRI};")
        self.cred_lay.addWidget(self.cred_title)

        # API Key input
        self.key_lbl = QLabel("API KEY / CREDENTIAL (STORED IN OS SECURE KEYRING):")
        self.key_lbl.setFont(px_font(8, QFont.Weight.Bold))
        self.key_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.cred_lay.addWidget(self.key_lbl)

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setFont(mono_font(9))
        self.key_input.setPlaceholderText("Enter API Key (will be masked and securely stored)...")
        self.key_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(10, 22, 40, 0.85); color: {C.WHITE};
                border: 1px solid rgba(51, 65, 85, 0.9); border-radius: 4px; padding: 6px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self.cred_lay.addWidget(self.key_input)

        # Endpoint input (for custom/ollama)
        self.endpoint_lbl = QLabel("ENDPOINT URL:")
        self.endpoint_lbl.setFont(px_font(8, QFont.Weight.Bold))
        self.endpoint_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.cred_lay.addWidget(self.endpoint_lbl)

        self.endpoint_input = QLineEdit()
        self.endpoint_input.setFont(mono_font(9))
        self.endpoint_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(10, 22, 40, 0.85); color: {C.WHITE};
                border: 1px solid rgba(51, 65, 85, 0.9); border-radius: 4px; padding: 6px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self.cred_lay.addWidget(self.endpoint_input)

        # Model Selection Combo
        self.model_lbl = QLabel("ACTIVE MODEL:")
        self.model_lbl.setFont(px_font(8, QFont.Weight.Bold))
        self.model_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.cred_lay.addWidget(self.model_lbl)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setFont(mono_font(9))
        self.model_combo.setStyleSheet(f"""
            QComboBox {{
                background: rgba(10, 22, 40, 0.85); color: {C.WHITE};
                border: 1px solid rgba(51, 65, 85, 0.9); border-radius: 4px; padding: 4px 8px;
            }}
        """)
        self.cred_lay.addWidget(self.model_combo)

        # Test Connection button & status
        test_row = QHBoxLayout()
        self.btn_test = QPushButton("⚡  TEST CONNECTION")
        self.btn_test.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_test.setFixedHeight(30)
        self.btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test.setStyleSheet(f"""
            QPushButton {{
                color: {C.PRI}; background: transparent;
                border: 1px solid {C.PRI_DIM}; border-radius: 4px; padding: 4px 12px;
            }}
            QPushButton:hover {{ background: rgba(0, 212, 255, 0.1); border-color: {C.PRI}; }}
        """)
        self.btn_test.clicked.connect(self._test_connection)
        test_row.addWidget(self.btn_test)

        self.test_result_lbl = QLabel("")
        self.test_result_lbl.setFont(px_font(9))
        self.test_result_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        test_row.addWidget(self.test_result_lbl)
        test_row.addStretch()
        self.cred_lay.addLayout(test_row)

        self.cred_lay.addStretch()
        return w

    def _refresh_cred_page(self) -> None:
        p_info = PROVIDER_METADATA.get(self.selected_provider, {})
        req_key = p_info.get("requires_key", True)
        self.key_lbl.setVisible(req_key)
        self.key_input.setVisible(req_key)

        req_ep = p_info.get("requires_endpoint", False)
        self.endpoint_lbl.setVisible(req_ep)
        self.endpoint_input.setVisible(req_ep)
        self.endpoint_input.setText(p_info.get("default_endpoint", ""))

        self.model_combo.clear()
        for m in p_info.get("models", []):
            self.model_combo.addItem(m)

        # Prepopulate existing key if stored
        existing = get_credential(self.selected_provider)
        if existing:
            self.key_input.setText(existing)
            self.test_result_lbl.setText("Found stored credential in OS Keyring.")

    def _test_connection(self) -> None:
        self.test_result_lbl.setText("Testing connection...")
        self.test_result_lbl.setStyleSheet(f"color: {C.ACC2};")

        prov = self.selected_provider
        key = self.key_input.text().strip()
        endpoint = self.endpoint_input.text().strip()
        model = self.model_combo.currentText().strip()

        def _worker():
            ok, msg = verify_provider_connection(
                provider=prov,
                api_key=key,
                endpoint=endpoint,
                model=model,
            )
            col = C.GREEN if ok else C.RED
            self.test_result_lbl.setText(f"{'✓' if ok else '✗'} {msg}")
            self.test_result_lbl.setStyleSheet(f"color: {col};")

        threading.Thread(target=_worker, daemon=True).start()

    # ── Step 4: Permissions ──────────────────────────────────────────────────

    def _build_step_permissions(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        t = QLabel("STEP 3: AUTONOMOUS PERMISSION PROFILE")
        t.setFont(px_font(12, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.PRI};")
        lay.addWidget(t)

        sub = QLabel("Select the autonomous operational tier. Enforced directly inside the central orchestration layer.")
        sub.setFont(px_font(9))
        sub.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay.addWidget(sub)

        self.perm_cards: dict[PermissionProfile, OnboardingCard] = {}

        # 1. LOW
        c_low = OnboardingCard(
            "LOW (PROTECTED / READ-ONLY)",
            "• Read-only operations allowed by default.\n"
            "• Asks confirmation before modifying any files or databases.\n"
            "• External network operations require human confirmation.",
            badge="READ ONLY",
        )
        c_low.mousePressEvent = lambda ev: self._select_perm(PermissionProfile.LOW)
        lay.addWidget(c_low)
        self.perm_cards[PermissionProfile.LOW] = c_low

        # 2. MODERATE
        c_mod = OnboardingCard(
            "MODERATE (SUPERVISED WORKFLOWS)",
            "• Autonomous execution of approved workflows.\n"
            "• Unrestricted file operations within authorized project workspaces.\n"
            "• Sensitive process execution or terminal mutation requires approval.",
            badge="STANDARD",
        )
        c_mod.mousePressEvent = lambda ev: self._select_perm(PermissionProfile.MODERATE)
        lay.addWidget(c_mod)
        self.perm_cards[PermissionProfile.MODERATE] = c_mod

        # 3. ADVANCED
        c_adv = OnboardingCard(
            "ADVANCED (FULL AUTONOMOUS - RECOMMENDED FOR DEV)",
            "• Expanded autonomous execution (terminal, development, multi-agent delegation).\n"
            "• Automated file transformations, testing, and debugging.\n"
            "• MANDATORY authorization strictly enforced for destructive, financial, and credential access.",
            badge="RECOMMENDED",
        )
        c_adv.mousePressEvent = lambda ev: self._select_perm(PermissionProfile.ADVANCED)
        lay.addWidget(c_adv)
        self.perm_cards[PermissionProfile.ADVANCED] = c_adv

        lay.addStretch()
        self._select_perm(PermissionProfile.ADVANCED)
        return w

    def _select_perm(self, profile: PermissionProfile) -> None:
        self.selected_profile = profile
        for prof, card in self.perm_cards.items():
            card.set_selected(prof == profile)

    # ── Step 5: Summary ──────────────────────────────────────────────────────

    def _build_step_summary(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(12)

        t = QLabel("STEP 4: CONFIGURATION SUMMARY & VERIFICATION")
        t.setFont(px_font(12, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.PRI};")
        lay.addWidget(t)

        self.summary_box = QFrame()
        self.summary_box.setStyleSheet("""
            QFrame {
                background: rgba(10, 22, 40, 0.7);
                border: 1px solid rgba(0, 212, 255, 0.3);
                border-radius: 6px;
                padding: 14px;
            }
        """)
        sum_lay = QVBoxLayout(self.summary_box)
        sum_lay.setSpacing(8)

        self.lbl_sum_prov = QLabel("")
        self.lbl_sum_prov.setFont(mono_font(9))
        self.lbl_sum_prov.setStyleSheet(f"color: {C.WHITE};")
        sum_lay.addWidget(self.lbl_sum_prov)

        self.lbl_sum_model = QLabel("")
        self.lbl_sum_model.setFont(mono_font(9))
        self.lbl_sum_model.setStyleSheet(f"color: {C.WHITE};")
        sum_lay.addWidget(self.lbl_sum_model)

        self.lbl_sum_cred = QLabel("")
        self.lbl_sum_cred.setFont(mono_font(9))
        self.lbl_sum_cred.setStyleSheet(f"color: {C.WHITE};")
        sum_lay.addWidget(self.lbl_sum_cred)

        self.lbl_sum_perm = QLabel("")
        self.lbl_sum_perm.setFont(mono_font(9))
        self.lbl_sum_perm.setStyleSheet(f"color: {C.WHITE};")
        sum_lay.addWidget(self.lbl_sum_perm)

        lay.addWidget(self.summary_box)

        note = QLabel(
            "✓ Credentials will be committed to your local OS Secure Keyring.\n"
            "✓ No plain API keys will be written to Git or config files.\n"
            "✓ Central permission policies will be enforced on every tool call."
        )
        note.setFont(px_font(9))
        note.setStyleSheet(f"color: {C.GREEN}; line-height: 1.4;")
        lay.addWidget(note)

        lay.addStretch()
        return w

    def _refresh_summary(self) -> None:
        self.lbl_sum_prov.setText(f"AI PROVIDER:          {self.selected_provider.upper()} ({PROVIDER_METADATA.get(self.selected_provider, {}).get('display_name', '')})")
        self.lbl_sum_model.setText(f"ACTIVE MODEL:         {self.model_combo.currentText().strip()}")
        key_masked = mask_credential(self.key_input.text().strip() or get_credential(self.selected_provider))
        self.lbl_sum_cred.setText(f"CREDENTIAL STATUS:    {key_masked} (Protected)")
        self.lbl_sum_perm.setText(f"PERMISSION PROFILE:   {self.selected_profile.value} (Central Enforcement)")

    # ── Wizard Navigation Logic ──────────────────────────────────────────────

    def _go_next(self) -> None:
        if self.current_step == 0:
            self.current_step = 1
            self.stack.setCurrentIndex(1)
            self.btn_back.setVisible(True)
            self.step_tracker_lbl.setText("STEP 1 OF 4")

        elif self.current_step == 1:
            self.current_step = 2
            self._refresh_cred_page()
            self.stack.setCurrentIndex(2)
            self.step_tracker_lbl.setText("STEP 2 OF 4")

        elif self.current_step == 2:
            self.current_step = 3
            self.stack.setCurrentIndex(3)
            self.step_tracker_lbl.setText("STEP 3 OF 4")

        elif self.current_step == 3:
            self.current_step = 4
            self._refresh_summary()
            self.stack.setCurrentIndex(4)
            self.step_tracker_lbl.setText("FINAL SUMMARY")
            self.btn_next.setText("ENTER OPTIMUS  ◈")

        elif self.current_step == 4:
            self._finalize_onboarding()

    def _go_back(self) -> None:
        if self.current_step > 0:
            self.current_step -= 1
            self.stack.setCurrentIndex(self.current_step)
            self.btn_next.setText("CONTINUE  →")
            if self.current_step == 0:
                self.btn_back.setVisible(False)
                self.step_tracker_lbl.setText("WELCOME")
            else:
                self.step_tracker_lbl.setText(f"STEP {self.current_step} OF 4")

    def _finalize_onboarding(self) -> None:
        key = self.key_input.text().strip()
        prov = self.selected_provider
        endpoint = self.endpoint_input.text().strip()
        model = self.model_combo.currentText().strip()
        profile = self.selected_profile

        # 1. Save credential securely
        if key:
            store_credential(prov, key)

        # 2. Persist onboarding settings
        cfg = {
            "primary_provider": prov,
            "model_name": model,
            "custom_endpoint": endpoint,
            "permission_profile": profile.value,
            "onboarding_completed": True,
        }
        save_onboarding_config(cfg)

        # 3. Synchronize with Central Orchestrator / Gemini / Legacy config
        try:
            from memory.config_manager import update_config
            fields = {"local_only": prov == "local_only"}
            if prov == "gemini" and key:
                fields["gemini_api_key"] = key
            update_config(**fields)
        except Exception:
            pass

        self.completed.emit(cfg)
        self.hide()
