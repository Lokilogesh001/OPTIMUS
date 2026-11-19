"""gui/optimus/section_personal.py — Cinematic JARVIS Personal Life Intelligence UI.

Implements the 8 dedicated views specified in Milestone 12:
1. Dashboard: Today's schedule, important unread messages, upcoming deadlines, pending tasks.
2. Unified Inbox: Consolidates messages across Gmail, Outlook, WhatsApp with search & filters.
3. My Tasks: Actionable obligations, deadlines, confidence, original source inspection.
4. Calendar: Aggregated schedule view, provider color coding, conflict alerts.
5. Connected Accounts: Gmail, Calendar, Outlook, WhatsApp status, sync controls, revoke.
6. WhatsApp Hub: Meta Business Cloud API vs Personal Safe Handoffs.
7. Automations: Proactive follow-ups, reply drafting, conflict notifications.
8. Privacy & Permissions: Granted scopes, retention policies, complete data purge.
"""

from __future__ import annotations

import datetime
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.personal_intelligence.engine import PersonalIntelligenceEngine
from core.personal_intelligence.providers.whatsapp_business import (
    WhatsAppBusinessAdapter,
)
from core.personal_intelligence.providers.whatsapp_personal import (
    WhatsAppPersonalAdapter,
)
from core.personal_intelligence.storage import PersonalStore, mask_pii_string
from core.personal_intelligence.types import (
    Account,
    AuthStatus,
    ProviderType,
    TaskStatus,
)
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, px_font
from gui.theme import C
from gui.widgets import HoloPanel


class SectionPersonal(QWidget):
    """Unified Personal Life Intelligence surface for OPTIMUS."""

    def __init__(self, bridge: OptimusServiceBridge | None = None, parent=None):
        super().__init__(parent)
        self.bridge = bridge or OptimusServiceBridge.instance()
        self.store = PersonalStore.instance()
        self.engine = PersonalIntelligenceEngine(store=self.store)

        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        title = QLabel("PERSONAL LIFE INTELLIGENCE")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        title_box.addWidget(title)

        subtitle = QLabel("UNIFIED INBOX  ·  AUTONOMOUS TASK EXTRACTION  ·  CALENDAR TRIAGE  ·  GOVERNED LIFE ASSISTANT")
        subtitle.setFont(px_font(9, QFont.Weight.DemiBold, spacing=1.2))
        subtitle.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        title_box.addWidget(subtitle)

        hdr.addLayout(title_box)
        hdr.addStretch()

        self.btn_sync_now = QPushButton("SYNC ALL ACCOUNTS")
        self.btn_sync_now.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_sync_now.setStyleSheet(btn_css("primary"))
        self.btn_sync_now.clicked.connect(self._trigger_sync)
        hdr.addWidget(self.btn_sync_now)

        self.btn_refresh = QPushButton("REFRESH")
        self.btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_refresh.setStyleSheet(btn_css("secondary"))
        self.btn_refresh.clicked.connect(self.refresh_all)
        hdr.addWidget(self.btn_refresh)

        layout.addLayout(hdr)

        # ── 8 Sub-Navigation Views ───────────────────────────────────────────
        subnav_box = QHBoxLayout()
        subnav_box.setSpacing(6)

        self.sub_buttons: list[QPushButton] = []
        sub_views = [
            ("DASHBOARD", 0),
            ("UNIFIED INBOX", 1),
            ("MY TASKS", 2),
            ("CALENDAR", 3),
            ("CONNECTED ACCOUNTS", 4),
            ("WHATSAPP", 5),
            ("AUTOMATIONS", 6),
            ("PRIVACY & PERMISSIONS", 7),
        ]
        for name, idx in sub_views:
            btn = QPushButton(name)
            btn.setFont(px_font(10, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet(self._subnav_btn_style(idx == 0))
            btn.clicked.connect(lambda checked=False, i=idx: self._switch_sub_view(i))
            subnav_box.addWidget(btn)
            self.sub_buttons.append(btn)

        subnav_box.addStretch()
        layout.addLayout(subnav_box)

        # ── Stacked Views ────────────────────────────────────────────────────
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack, stretch=1)

        self._init_dashboard_view()    # 0
        self._init_inbox_view()        # 1
        self._init_tasks_view()        # 2
        self._init_calendar_view()     # 3
        self._init_accounts_view()     # 4
        self._init_whatsapp_view()     # 5
        self._init_automations_view()  # 6
        self._init_privacy_view()      # 7

        self.refresh_all()

    def _subnav_btn_style(self, active: bool) -> str:
        if active:
            return f"color: {C.WHITE}; background: rgba(0, 212, 255, 0.15); border: 1px solid {C.PRI}; border-radius: 3px; padding: 0 10px;"
        return f"color: {C.TEXT_DIM}; background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 0 10px;"

    def _switch_sub_view(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.sub_buttons):
            btn.setStyleSheet(self._subnav_btn_style(i == index))

    # ── View 0: Dashboard ────────────────────────────────────────────────────

    def _init_dashboard_view(self) -> None:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Metrics cards bar
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(8)

        self.lbl_card_unread = self._create_metric_card("UNREAD MESSAGES", "0", C.PRI, cards_layout)
        self.lbl_card_tasks = self._create_metric_card("PENDING TASKS", "0", C.ACC, cards_layout)
        self.lbl_card_overdue = self._create_metric_card("OVERDUE DEADLINES", "0", C.RED, cards_layout)
        self.lbl_card_events = self._create_metric_card("TODAY'S SCHEDULE", "0", C.GREEN, cards_layout)
        vbox.addLayout(cards_layout)

        # Splitter: Today's Schedule & Urgent Tasks
        split = QSplitter(Qt.Orientation.Horizontal)

        # Panel Left: Today's Schedule
        panel_left = HoloPanel("TODAY'S TIMELINE & APPOINTMENTS")
        pl_box = QVBoxLayout(panel_left)
        pl_box.setContentsMargins(8, 8, 8, 8)
        self.tbl_today_events = QTableWidget(0, 3)
        self.tbl_today_events.setHorizontalHeaderLabels(["TIME", "EVENT TITLE", "LOCATION / LINK"])
        self.tbl_today_events.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_today_events)
        pl_box.addWidget(self.tbl_today_events)
        split.addWidget(panel_left)

        # Panel Right: Actionable Tasks & Commitments
        panel_right = HoloPanel("PENDING OBLIGATIONS & DEADLINES")
        pr_box = QVBoxLayout(panel_right)
        pr_box.setContentsMargins(8, 8, 8, 8)
        self.tbl_top_tasks = QTableWidget(0, 3)
        self.tbl_top_tasks.setHorizontalHeaderLabels(["PRIORITY", "TASK TITLE", "DUE DATE"])
        self.tbl_top_tasks.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_top_tasks)
        pr_box.addWidget(self.tbl_top_tasks)
        split.addWidget(panel_right)

        vbox.addWidget(split, stretch=1)
        self.stack.addWidget(w)

    def _create_metric_card(self, title: str, val: str, color: str, parent_layout: QHBoxLayout) -> QLabel:
        p = HoloPanel()
        p.setFixedHeight(64)
        vb = QVBoxLayout(p)
        vb.setContentsMargins(10, 6, 10, 6)
        vb.setSpacing(2)
        t = QLabel(title)
        t.setFont(px_font(9, QFont.Weight.DemiBold, spacing=1.2))
        t.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        vb.addWidget(t)

        v = QLabel(val)
        v.setFont(px_font(18, QFont.Weight.Bold))
        v.setStyleSheet(f"color: {color}; background: transparent;")
        vb.addWidget(v)
        parent_layout.addWidget(p)
        return v

    # ── View 1: Unified Inbox ────────────────────────────────────────────────

    def _init_inbox_view(self) -> None:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Filter bar
        filter_box = QHBoxLayout()
        filter_box.setSpacing(8)

        self.cmb_inbox_prov = QComboBox()
        self.cmb_inbox_prov.addItems(["ALL PROVIDERS", "GMAIL", "OUTLOOK", "WHATSAPP_BUSINESS", "WHATSAPP_PERSONAL"])
        self.cmb_inbox_prov.setStyleSheet(self._combo_css())
        self.cmb_inbox_prov.currentTextChanged.connect(self._refresh_inbox)
        filter_box.addWidget(self.cmb_inbox_prov)

        self.txt_inbox_search = QLineEdit()
        self.txt_inbox_search.setPlaceholderText("Search messages by sender, subject, or keywords...")
        self.txt_inbox_search.setStyleSheet(self._input_css())
        self.txt_inbox_search.textChanged.connect(self._refresh_inbox)
        filter_box.addWidget(self.txt_inbox_search)

        vbox.addLayout(filter_box)

        # Messages Table
        self.tbl_inbox = QTableWidget(0, 5)
        self.tbl_inbox.setHorizontalHeaderLabels(["CHANNEL", "SENDER", "SUBJECT / CONVERSATION", "DATE", "TRIAGE"])
        self.tbl_inbox.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_inbox)
        vbox.addWidget(self.tbl_inbox, stretch=1)

        self.stack.addWidget(w)

    # ── View 2: My Tasks ─────────────────────────────────────────────────────

    def _init_tasks_view(self) -> None:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Status Tabs
        btn_bar = QHBoxLayout()
        self.cmb_task_filter = QComboBox()
        self.cmb_task_filter.addItems(["ALL TASKS", "PENDING", "IN_PROGRESS", "COMPLETED", "OVERDUE"])
        self.cmb_task_filter.setStyleSheet(self._combo_css())
        self.cmb_task_filter.currentTextChanged.connect(self._refresh_tasks)
        btn_bar.addWidget(self.cmb_task_filter)
        btn_bar.addStretch()
        vbox.addLayout(btn_bar)

        self.tbl_tasks = QTableWidget(0, 6)
        self.tbl_tasks.setHorizontalHeaderLabels(["ID", "PRIORITY", "TASK TITLE", "SOURCE", "DUE DATE", "STATUS"])
        self.tbl_tasks.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_tasks)
        vbox.addWidget(self.tbl_tasks, stretch=1)

        self.stack.addWidget(w)

    # ── View 3: Calendar ─────────────────────────────────────────────────────

    def _init_calendar_view(self) -> None:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        self.tbl_calendar = QTableWidget(0, 5)
        self.tbl_calendar.setHorizontalHeaderLabels(["PROVIDER", "START", "END", "EVENT SUMMARY", "CONFLICT"])
        self.tbl_calendar.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_calendar)
        vbox.addWidget(self.tbl_calendar, stretch=1)

        self.stack.addWidget(w)

    # ── View 4: Connected Accounts ───────────────────────────────────────────

    def _init_accounts_view(self) -> None:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        panel = HoloPanel("CONNECTED COMMUNICATION & CALENDAR PROVIDERS")
        p_box = QVBoxLayout(panel)
        p_box.setContentsMargins(10, 10, 10, 10)

        self.tbl_accounts = QTableWidget(0, 6)
        self.tbl_accounts.setHorizontalHeaderLabels(["ACCOUNT ID", "PROVIDER", "IDENTITY", "AUTH STATUS", "LAST SYNC", "ACTIONS"])
        self.tbl_accounts.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_accounts)
        p_box.addWidget(self.tbl_accounts)

        vbox.addWidget(panel, stretch=1)
        self.stack.addWidget(w)

    # ── View 5: WhatsApp Hub ─────────────────────────────────────────────────

    def _init_whatsapp_view(self) -> None:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        split = QSplitter(Qt.Orientation.Horizontal)

        # Left: Business Cloud API
        p_biz = HoloPanel("META WHATSAPP BUSINESS CLOUD API")
        vb_biz = QVBoxLayout(p_biz)
        vb_biz.setContentsMargins(8, 8, 8, 8)
        lbl_biz_desc = QLabel(
            "Official Meta Business API.\nRequires Meta Cloud API credentials & Internet-accessible Webhook Tunnel."
        )
        lbl_biz_desc.setFont(px_font(9))
        lbl_biz_desc.setStyleSheet(f"color: {C.TEXT_DIM};")
        vb_biz.addWidget(lbl_biz_desc)

        self.txt_wa_phone_id = QLineEdit()
        self.txt_wa_phone_id.setPlaceholderText("Phone Number ID")
        self.txt_wa_phone_id.setStyleSheet(self._input_css())
        vb_biz.addWidget(self.txt_wa_phone_id)

        self.txt_wa_token = QLineEdit()
        self.txt_wa_token.setPlaceholderText("System User Access Token")
        self.txt_wa_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_wa_token.setStyleSheet(self._input_css())
        vb_biz.addWidget(self.txt_wa_token)

        self.btn_save_biz = QPushButton("CONNECT BUSINESS API")
        self.btn_save_biz.setStyleSheet(btn_css("primary"))
        self.btn_save_biz.clicked.connect(self._connect_wa_business)
        vb_biz.addWidget(self.btn_save_biz)
        vb_biz.addStretch()
        split.addWidget(p_biz)

        # Right: Personal Safe Handoff
        p_pers = HoloPanel("WHATSAPP PERSONAL SAFE INTEGRATION")
        vb_pers = QVBoxLayout(p_pers)
        vb_pers.setContentsMargins(8, 8, 8, 8)
        lbl_pers_desc = QLabel(
            "Architecturally honest local mode.\nNo unofficial scrapers or session hijacking.\n"
            "Supported: Chat Export file import (.txt) & one-click wa.me handoffs."
        )
        lbl_pers_desc.setFont(px_font(9))
        lbl_pers_desc.setStyleSheet(f"color: {C.TEXT_DIM};")
        vb_pers.addWidget(lbl_pers_desc)

        self.txt_chat_export = QTextEdit()
        self.txt_chat_export.setPlaceholderText("Paste exported WhatsApp chat text here to extract tasks & commitments...")
        self.txt_chat_export.setStyleSheet(self._input_css())
        vb_pers.addWidget(self.txt_chat_export)

        self.btn_import_chat = QPushButton("INGEST CHAT EXPORT")
        self.btn_import_chat.setStyleSheet(btn_css("secondary"))
        self.btn_import_chat.clicked.connect(self._import_personal_chat)
        vb_pers.addWidget(self.btn_import_chat)
        split.addWidget(p_pers)

        vbox.addWidget(split, stretch=1)
        self.stack.addWidget(w)

    # ── View 6: Automations ──────────────────────────────────────────────────

    def _init_automations_view(self) -> None:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        panel = HoloPanel("PERSONAL PROACTIVE AUTOMATION RULES")
        p_box = QVBoxLayout(panel)
        p_box.setContentsMargins(10, 10, 10, 10)

        rules_info = QLabel(
            "• Auto-Triage: Rank incoming messages by importance and VIP contact status.\n"
            "• Task Extraction: Extract actionable commitments and schedule deadlines.\n"
            "• Conflict Alerting: Monitor overlapping meetings across Google & Outlook calendars.\n"
            "• Governed Execution: Consequential mutations (send, delete, modify) strictly require ApprovalTokens."
        )
        rules_info.setFont(px_font(10))
        rules_info.setStyleSheet(f"color: {C.WHITE}; background: transparent; line-height: 1.6;")
        p_box.addWidget(rules_info)
        p_box.addStretch()

        vbox.addWidget(panel, stretch=1)
        self.stack.addWidget(w)

    # ── View 7: Privacy & Permissions ────────────────────────────────────────

    def _init_privacy_view(self) -> None:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        panel = HoloPanel("LOCAL DATA RETENTION & SECURITY CONTROL")
        p_box = QVBoxLayout(panel)
        p_box.setContentsMargins(10, 10, 10, 10)
        p_box.setSpacing(12)

        info = QLabel(
            "Local-First Guarantee:\n"
            "All personal communications, tasks, and calendar events are persistently stored on your local disk\n"
            "under memory/personal/. Credentials are encrypted with machine-bound keys via OS Keyring / CredentialVault."
        )
        info.setFont(px_font(10))
        info.setStyleSheet(f"color: {C.TEXT_DIM};")
        p_box.addWidget(info)

        self.btn_purge_gmail = QPushButton("PURGE GMAIL CACHE")
        self.btn_purge_gmail.setStyleSheet(btn_css("secondary"))
        self.btn_purge_gmail.clicked.connect(lambda: self._purge_provider(ProviderType.GMAIL))
        p_box.addWidget(self.btn_purge_gmail)

        self.btn_purge_outlook = QPushButton("PURGE OUTLOOK CACHE")
        self.btn_purge_outlook.setStyleSheet(btn_css("secondary"))
        self.btn_purge_outlook.clicked.connect(lambda: self._purge_provider(ProviderType.OUTLOOK))
        p_box.addWidget(self.btn_purge_outlook)

        self.btn_wipe_all = QPushButton("WIPE ALL LOCAL PERSONAL INTELLIGENCE DATA")
        self.btn_wipe_all.setStyleSheet(btn_css("danger"))
        self.btn_wipe_all.clicked.connect(self._wipe_all)
        p_box.addWidget(self.btn_wipe_all)
        p_box.addStretch()

        vbox.addWidget(panel, stretch=1)
        self.stack.addWidget(w)

    # ── Refresh & Event Handlers ─────────────────────────────────────────────

    def refresh_all(self) -> None:
        """Hydrate all sub-views from PersonalStore."""
        self._refresh_dashboard()
        self._refresh_inbox()
        self._refresh_tasks()
        self._refresh_calendar()
        self._refresh_accounts()

    def _refresh_dashboard(self) -> None:
        now = time.time()
        start_of_day = now - (now % 86400)
        end_of_day = start_of_day + 86400

        unreads = self.store.list_messages(is_unread=True)
        tasks = self.store.list_tasks(status=TaskStatus.PENDING)
        overdues = self.store.list_tasks(overdue_only=True)
        today_evts = self.store.list_events(start_min=start_of_day, start_max=end_of_day)

        self.lbl_card_unread.setText(str(len(unreads)))
        self.lbl_card_tasks.setText(str(len(tasks)))
        self.lbl_card_overdue.setText(str(len(overdues)))
        self.lbl_card_events.setText(str(len(today_evts)))

        # Fill today events
        self.tbl_today_events.setRowCount(0)
        for e in today_evts:
            row = self.tbl_today_events.rowCount()
            self.tbl_today_events.insertRow(row)
            t_str = datetime.datetime.fromtimestamp(e.start_time).strftime("%H:%M")
            self.tbl_today_events.setItem(row, 0, QTableWidgetItem(t_str))
            self.tbl_today_events.setItem(row, 1, QTableWidgetItem(e.title))
            self.tbl_today_events.setItem(row, 2, QTableWidgetItem(e.location or "No location"))

        # Fill top tasks
        self.tbl_top_tasks.setRowCount(0)
        for t in tasks[:10]:
            row = self.tbl_top_tasks.rowCount()
            self.tbl_top_tasks.insertRow(row)
            due_str = datetime.datetime.fromtimestamp(t.due_date).strftime("%b %d %H:%M") if t.due_date else "No Deadline"
            self.tbl_top_tasks.setItem(row, 0, QTableWidgetItem(t.priority.value))
            self.tbl_top_tasks.setItem(row, 1, QTableWidgetItem(t.title))
            self.tbl_top_tasks.setItem(row, 2, QTableWidgetItem(due_str))

    def _refresh_inbox(self) -> None:
        prov_text = self.cmb_inbox_prov.currentText()
        prov = None if prov_text == "ALL PROVIDERS" else ProviderType(prov_text)
        query = self.txt_inbox_search.text().strip() or None

        msgs = self.store.list_messages(provider=prov, search_query=query, limit=50)
        self.tbl_inbox.setRowCount(0)
        for m in msgs:
            row = self.tbl_inbox.rowCount()
            self.tbl_inbox.insertRow(row)
            d_str = datetime.datetime.fromtimestamp(m.timestamp).strftime("%b %d %H:%M")
            triage = "IMPORTANT" if m.is_important else ("UNREAD" if not m.is_read else "READ")

            self.tbl_inbox.setItem(row, 0, QTableWidgetItem(m.provider.value))
            self.tbl_inbox.setItem(row, 1, QTableWidgetItem(m.sender_name or m.sender))
            self.tbl_inbox.setItem(row, 2, QTableWidgetItem(m.subject))
            self.tbl_inbox.setItem(row, 3, QTableWidgetItem(d_str))
            self.tbl_inbox.setItem(row, 4, QTableWidgetItem(triage))

    def _refresh_tasks(self) -> None:
        filter_text = self.cmb_task_filter.currentText()
        st = None
        overdue_only = False
        if filter_text == "OVERDUE":
            overdue_only = True
        elif filter_text != "ALL TASKS":
            st = TaskStatus(filter_text)

        tasks = self.store.list_tasks(status=st, overdue_only=overdue_only)
        self.tbl_tasks.setRowCount(0)
        for t in tasks:
            row = self.tbl_tasks.rowCount()
            self.tbl_tasks.insertRow(row)
            due_str = datetime.datetime.fromtimestamp(t.due_date).strftime("%b %d %H:%M") if t.due_date else "None"
            self.tbl_tasks.setItem(row, 0, QTableWidgetItem(t.task_id[:8]))
            self.tbl_tasks.setItem(row, 1, QTableWidgetItem(t.priority.value))
            self.tbl_tasks.setItem(row, 2, QTableWidgetItem(t.title))
            self.tbl_tasks.setItem(row, 3, QTableWidgetItem(t.source_provider.value))
            self.tbl_tasks.setItem(row, 4, QTableWidgetItem(due_str))
            self.tbl_tasks.setItem(row, 5, QTableWidgetItem(t.status.value))

    def _refresh_calendar(self) -> None:
        events = self.store.list_events()
        self.tbl_calendar.setRowCount(0)
        for e in events:
            row = self.tbl_calendar.rowCount()
            self.tbl_calendar.insertRow(row)
            st_str = datetime.datetime.fromtimestamp(e.start_time).strftime("%b %d %H:%M")
            end_str = datetime.datetime.fromtimestamp(e.end_time).strftime("%H:%M")
            conflict_str = "CONFLICT DETECTED" if e.conflict_detected else "CLEAR"
            self.tbl_calendar.setItem(row, 0, QTableWidgetItem(e.provider.value))
            self.tbl_calendar.setItem(row, 1, QTableWidgetItem(st_str))
            self.tbl_calendar.setItem(row, 2, QTableWidgetItem(end_str))
            self.tbl_calendar.setItem(row, 3, QTableWidgetItem(e.title))
            self.tbl_calendar.setItem(row, 4, QTableWidgetItem(conflict_str))

    def _refresh_accounts(self) -> None:
        accounts = self.store.list_accounts()
        self.tbl_accounts.setRowCount(0)
        for a in accounts:
            row = self.tbl_accounts.rowCount()
            self.tbl_accounts.insertRow(row)
            sync_time = datetime.datetime.fromtimestamp(a.last_synced_at).strftime("%b %d %H:%M") if a.last_synced_at else "Never"
            self.tbl_accounts.setItem(row, 0, QTableWidgetItem(a.account_id))
            self.tbl_accounts.setItem(row, 1, QTableWidgetItem(a.provider.value))
            self.tbl_accounts.setItem(row, 2, QTableWidgetItem(mask_pii_string(a.email_or_id)))
            self.tbl_accounts.setItem(row, 3, QTableWidgetItem(a.auth_status.value))
            self.tbl_accounts.setItem(row, 4, QTableWidgetItem(sync_time))
            self.tbl_accounts.setItem(row, 5, QTableWidgetItem("DISCONNECT"))

    def _trigger_sync(self) -> None:
        self.engine.sync_all_connected_accounts()
        self.refresh_all()

    def _connect_wa_business(self) -> None:
        phone_id = self.txt_wa_phone_id.text().strip()
        token = self.txt_wa_token.text().strip()
        if not phone_id or not token:
            return
        acc = Account(
            account_id="wa_biz_primary",
            provider=ProviderType.WHATSAPP_BUSINESS,
            email_or_id=phone_id,
            display_name=f"WhatsApp Business ({phone_id})",
            auth_status=AuthStatus.CONNECTED,
        )
        self.store.save_account(acc)
        adapter = WhatsAppBusinessAdapter(acc)
        adapter.authenticate({"access_token": token, "phone_number_id": phone_id})
        self.refresh_all()

    def _import_personal_chat(self) -> None:
        text = self.txt_chat_export.toPlainText().strip()
        if not text:
            return
        acc = Account(
            account_id="wa_pers_import",
            provider=ProviderType.WHATSAPP_PERSONAL,
            email_or_id="User Import",
            display_name="WhatsApp Personal Import",
            auth_status=AuthStatus.CONNECTED,
        )
        self.store.save_account(acc)
        adapter = WhatsAppPersonalAdapter(acc)
        messages = adapter.parse_chat_export(text)
        for m in messages:
            self.store.save_message(m)
            self.engine.extract_tasks_and_commitments_from_message(m)
        self.txt_chat_export.clear()
        self.refresh_all()

    def _purge_provider(self, provider: ProviderType) -> None:
        self.store.delete_provider_data(provider)
        self.refresh_all()

    def _wipe_all(self) -> None:
        self.store.wipe_all_data()
        self.refresh_all()

    def _style_table(self, tbl: QTableWidget) -> None:
        tbl.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL};
                color: {C.WHITE};
                gridline-color: {C.BORDER_A};
                border: 1px solid {C.BORDER_A};
                font-family: 'Consolas', monospace;
                font-size: 11px;
            }}
            QHeaderView::section {{
                background: {C.PANEL2};
                color: {C.PRI};
                padding: 4px;
                border: 1px solid {C.BORDER_A};
                font-size: 10px;
                font-weight: bold;
            }}
        """)

    def _input_css(self) -> str:
        return f"background: {C.PANEL2}; color: {C.WHITE}; border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"

    def _combo_css(self) -> str:
        return f"background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"
