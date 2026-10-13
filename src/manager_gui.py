"""NBR Automation Manager 1.2: universal farming and passive session loot history."""

from __future__ import annotations

import sys
import math
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
    QHeaderView, QScrollArea, QTableWidget, QTableWidgetItem, QToolButton,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView,
)


class _Results(QObject):
    finished = Signal(str, object, object)


class ManagerWindow(QMainWindow):
    """The backend is injectable so GUI tests never need a live game or controller."""

    COLORS = {
        "STOPPED": "#8994a7", "STARTING": "#f5c764", "RUNNING": "#5adea5",
        "STOPPING": "#f5c764", "ERROR": "#ff858d",
    }

    def __init__(self, backend=None, poll_interval_ms=2000, auto_poll=True):
        super().__init__()
        if backend is None:
            from manager_backend import ControllerBackend
            backend = ControllerBackend()
        self.backend = backend
        self._closed = False
        self._snapshot_known = False
        self._snapshot = {}
        self._active = False
        self._connection = {}
        self._pending_action = None
        self._awaiting_verification = False
        self._revision = 0
        self._poll_busy = False
        self._poll_again = False
        self._connection_busy = False
        self._command_error = ""
        self._poll_error = ""
        self._exit_error = ""
        self._stage_error = ""
        self._desired_stage = "universal"
        self._started_at = None
        self._elapsed_seconds = 0
        self._state = "STOPPED"
        self._last_event_error = ""
        self._loot_busy = False
        self._loot_again = False
        self._loot_data = {}
        self._loot_session_id = None
        self._loot_offset = 0
        self._loot_limit = 100
        self._loot_request = None
        self._bridge = _Results(self)
        self._bridge.finished.connect(self._on_result)
        self._build_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(max(50, int(poll_interval_ms)))
        self.poll_timer.timeout.connect(self.refresh)
        self.connection_timer = QTimer(self)
        self.connection_timer.setInterval(15000)
        self.connection_timer.timeout.connect(self.refresh_connection)
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setInterval(1000)
        self.runtime_timer.timeout.connect(self._update_runtime)
        self.runtime_timer.start()
        self._run("stages", self.backend.stages)
        if auto_poll:
            self.poll_timer.start()
            self.connection_timer.start()
            self.refresh()
            self.refresh_connection()
        self._update_controls()

    @property
    def status_text(self):
        return self._state

    def _build_ui(self):
        self.setWindowTitle("NBR Automation Manager")
        self.resize(680, 740)
        self.setMinimumSize(560, 610)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #10151e; color: #e7edf7; }
            QLabel { background: transparent; }
            QLabel#muted { color: #9caac0; }
            QLabel#title { font-size: 23px; font-weight: 650; }
            QLabel#section { color: #9caac0; font-size: 11px; font-weight: 650; }
            QFrame#card { background: #19212f; border: 1px solid #293448; border-radius: 12px; }
            QComboBox { background: #202b3d; border: 1px solid #40516c; border-radius: 7px;
                        padding: 11px; min-height: 20px; }
            QComboBox:focus { border: 1px solid #84b5ff; }
            QComboBox:disabled { color: #a3aec0; background: #1a2433; }
            QComboBox QAbstractItemView { background: #202b3d; color: #e7edf7;
                                         selection-background-color: #355687; }
            QPushButton { border-radius: 8px; border: 1px solid #415470;
                          background: #26354b; padding: 11px; font-size: 15px; font-weight: 650; }
            QPushButton:hover { background: #344866; }
            QPushButton#start { background: #70d9af; color: #11281f; border-color: #70d9af; }
            QPushButton#start:hover { background: #93e8c7; }
            QPushButton#stop { color: #ffb9bf; border-color: #814751; }
            QPushButton:disabled, QPushButton#start:disabled, QPushButton#stop:disabled {
                background: #1c2635; color: #64738b; border-color: #2b384c; }
            QPlainTextEdit { background: #101720; color: #b7c8dd; border: 1px solid #293448;
                             border-radius: 8px; padding: 8px; selection-background-color: #355687; }
            QTableWidget, QTreeWidget { background: #101720; color: #b7c8dd;
                                       border: 1px solid #293448; alternate-background-color: #19212f; }
            QHeaderView::section { background: #202b3d; color: #e7edf7;
                                   border: 1px solid #293448; padding: 5px; }
            QScrollBar:vertical { background: #151d29; width: 10px; }
            QScrollBar::handle:vertical { background: #40516b; border-radius: 4px; min-height: 24px; }
        """)
        central = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(15)

        header = QHBoxLayout()
        title = QLabel("NBR Automation Manager")
        title.setObjectName("title")
        header.addWidget(title)
        version = QLabel("v1.2")
        version.setObjectName("muted")
        header.addStretch()
        header.addWidget(version)
        layout.addLayout(header)

        account_card = QFrame()
        account_card.setObjectName("card")
        account_layout = QVBoxLayout(account_card)
        account_layout.setContentsMargins(16, 13, 16, 13)
        account_line = QHBoxLayout()
        account = QLabel("ACCOUNT  ·  acc1")
        account.setStyleSheet("font-weight: 650;")
        self.connection_label = QLabel("Checking connection…")
        self.connection_label.setStyleSheet("color: #9caac0;")
        account_line.addWidget(account)
        account_line.addStretch()
        account_line.addWidget(self.connection_label)
        account_layout.addLayout(account_line)
        self.instance_label = QLabel("BlueStacks instance: checking…")
        self.instance_label.setObjectName("muted")
        self.instance_label.setWordWrap(True)
        account_layout.addWidget(self.instance_label)
        layout.addWidget(account_card)

        status_line = QHBoxLayout()
        self.status_label = QLabel("Checking process…")
        self.status_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #8994a7;")
        status_line.addWidget(self.status_label)
        status_line.addStretch()
        runtime_group = QVBoxLayout()
        runtime_caption = QLabel("ELAPSED FARMING TIME")
        runtime_caption.setObjectName("section")
        self.runtime_label = QLabel("00:00:00")
        self.runtime_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.runtime_label.setStyleSheet("font-size: 21px; font-weight: 600;")
        runtime_group.addWidget(runtime_caption)
        runtime_group.addWidget(self.runtime_label)
        status_line.addLayout(runtime_group)
        layout.addLayout(status_line)

        stage_caption = QLabel("FARMING MODE  ·  LEGACY STAGES OPTIONAL")
        stage_caption.setObjectName("section")
        layout.addWidget(stage_caption)
        self.stage_combo = QComboBox()
        self.stage_combo.setAccessibleName("Farming mode or optional legacy stage")
        self.stage_combo.setToolTip("Stage selection is locked while acc1 automation is active.")
        self.stage_combo.currentIndexChanged.connect(self._stage_changed)
        layout.addWidget(self.stage_combo)
        self.current_stage_label = QLabel("Current stage: checking…")
        self.current_stage_label.setTextFormat(Qt.TextFormat.PlainText)
        self.current_stage_label.setObjectName("muted")
        layout.addWidget(self.current_stage_label)
        stage_help = QLabel("Universal: open any mission’s Start Mission screen in acc1, then press Start. The game handles combat automatically. Optional legacy Practice Training and Night of Tragedy modes still require manually beginning the first battle.")
        stage_help.setObjectName("muted")
        stage_help.setWordWrap(True)
        layout.addWidget(stage_help)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.start_button = QPushButton("Start farming")
        self.start_button.setObjectName("start")
        self.start_button.setMinimumHeight(51)
        self.start_button.setToolTip("Start the existing acc1 controller. It retries BlueStacks connection and verifies device identity before game input.")
        self.start_button.clicked.connect(self.start_automation)
        self.stop_button = QPushButton("Stop farming")
        self.stop_button.setObjectName("stop")
        self.stop_button.setMinimumHeight(51)
        self.stop_button.clicked.connect(self.stop_automation)
        self.restart_button = QPushButton("Full Restart")
        self.restart_button.setMinimumHeight(51)
        self.restart_button.setToolTip("Safely stop and confirm all old farming processes have exited, then relaunch with device identity checks.")
        self.restart_button.clicked.connect(self.restart_automation)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.restart_button)
        layout.addLayout(controls)

        stamina_card = QFrame()
        stamina_card.setObjectName("card")
        stamina_layout = QVBoxLayout(stamina_card)
        stamina_layout.setContentsMargins(16, 13, 16, 13)
        stamina_line = QHBoxLayout()
        stamina_caption = QLabel("STAMINA")
        stamina_caption.setObjectName("section")
        self.stamina_status_label = QLabel("UNKNOWN")
        self.stamina_status_label.setToolTip("READY means the controller's wait has elapsed; fresh game and stamina checks still gate farming.")
        self.stamina_remaining_label = QLabel("Remaining: —")
        stamina_line.addWidget(stamina_caption)
        stamina_line.addWidget(self.stamina_status_label)
        stamina_line.addStretch()
        stamina_line.addWidget(self.stamina_remaining_label)
        stamina_layout.addLayout(stamina_line)
        stamina_controls = QHBoxLayout()
        self.stamina_duration_combo = QComboBox()
        self.stamina_duration_combo.setAccessibleName("Stamina wait duration")
        self.stamina_duration_combo.setToolTip("Saved by the controller; applies to subsequent stamina waits.")
        for minutes in (5, 10, 15, 20, 25):
            self.stamina_duration_combo.addItem(f"{minutes} minutes", minutes)
        self.stamina_duration_combo.currentIndexChanged.connect(self._stamina_duration_changed)
        self.end_stamina_wait_button = QPushButton("End Stamina Wait")
        self.end_stamina_wait_button.setToolTip("Interrupt the controller's wait and recheck game state and stamina. Insufficient stamina starts another wait.")
        self.end_stamina_wait_button.clicked.connect(self.end_stamina_wait)
        stamina_controls.addWidget(self.stamina_duration_combo)
        stamina_controls.addWidget(self.end_stamina_wait_button)
        stamina_layout.addLayout(stamina_controls)
        layout.addWidget(stamina_card)
        self.detail_label = QLabel("Reading existing automation state…")
        self.detail_label.setObjectName("muted")
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #ff9aa2;")
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        loot_card = QFrame()
        loot_card.setObjectName("card")
        loot_layout = QVBoxLayout(loot_card)
        loot_layout.setContentsMargins(16, 13, 16, 13)
        loot_header = QHBoxLayout()
        loot_caption = QLabel("SESSION LOOT")
        loot_caption.setObjectName("section")
        self.loot_status_label = QLabel("Tracking Unavailable")
        self.loot_status_label.setTextFormat(Qt.TextFormat.PlainText)
        loot_header.addWidget(loot_caption)
        loot_header.addStretch()
        loot_header.addWidget(self.loot_status_label)
        loot_layout.addLayout(loot_header)
        self.loot_summary_label = QLabel("Waiting for persisted loot records.")
        self.loot_summary_label.setWordWrap(True)
        self.loot_summary_label.setTextFormat(Qt.TextFormat.PlainText)
        loot_layout.addWidget(self.loot_summary_label)
        self.loot_detail_label = QLabel()
        self.loot_detail_label.setObjectName("muted")
        self.loot_detail_label.setWordWrap(True)
        self.loot_detail_label.setTextFormat(Qt.TextFormat.PlainText)
        loot_layout.addWidget(self.loot_detail_label)
        self.loot_history_toggle = QToolButton()
        self.loot_history_toggle.setText("Loot history")
        self.loot_history_toggle.setCheckable(True)
        self.loot_history_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.loot_history_toggle.setArrowType(Qt.ArrowType.RightArrow)
        loot_layout.addWidget(self.loot_history_toggle)
        self.loot_history = QWidget()
        history_layout = QVBoxLayout(self.loot_history)
        history_layout.setContentsMargins(0, 0, 0, 0)
        self.loot_session_combo = QComboBox()
        self.loot_session_combo.setAccessibleName("Loot tracking session")
        self.loot_session_combo.addItem("Current farming process", None)
        self.loot_session_combo.currentIndexChanged.connect(self._loot_session_changed)
        history_layout.addWidget(self.loot_session_combo)
        self.loot_session_label = QLabel()
        self.loot_session_label.setObjectName("muted")
        self.loot_session_label.setWordWrap(True)
        self.loot_session_label.setTextFormat(Qt.TextFormat.PlainText)
        history_layout.addWidget(self.loot_session_label)
        self.loot_item_table = QTableWidget(0, 3)
        self.loot_item_table.setHorizontalHeaderLabels(["Item / Character", "Observed Icons", "Confirmed Acquired"])
        self.loot_item_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.loot_item_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.loot_item_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.loot_item_table.verticalHeader().hide()
        self.loot_item_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.loot_item_table.setMinimumHeight(145)
        self.loot_item_table.setMaximumHeight(220)
        history_layout.addWidget(self.loot_item_table)
        self.loot_run_tree = QTreeWidget()
        self.loot_run_tree.setHeaderLabels(["Run / reward", "Completed / observed", "Status / acquired"])
        self.loot_run_tree.header().setStretchLastSection(False)
        self.loot_run_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.loot_run_tree.setColumnWidth(1, 175)
        self.loot_run_tree.setColumnWidth(2, 160)
        self.loot_run_tree.setMinimumHeight(170)
        self.loot_run_tree.setMaximumHeight(280)
        self.loot_run_tree.itemDoubleClicked.connect(self._open_loot_screenshot)
        history_layout.addWidget(self.loot_run_tree)
        history_pages = QHBoxLayout()
        self.loot_previous_button = QPushButton("Newer runs")
        self.loot_previous_button.clicked.connect(lambda: self._change_loot_page(-1))
        self.loot_next_button = QPushButton("Older runs")
        self.loot_next_button.clicked.connect(lambda: self._change_loot_page(1))
        self.loot_page_label = QLabel("No runs")
        self.loot_page_label.setObjectName("muted")
        history_pages.addWidget(self.loot_previous_button)
        history_pages.addWidget(self.loot_page_label, 1)
        history_pages.addWidget(self.loot_next_button)
        self.loot_previous_button.setEnabled(False)
        self.loot_next_button.setEnabled(False)
        history_layout.addLayout(history_pages)
        history_note = QLabel("Confirmed acquired values include verified quantities only; unknown quantities remain excluded. Expand runs for evidence; double-click a screenshot row to open it.")
        history_note.setObjectName("muted")
        history_note.setWordWrap(True)
        history_layout.addWidget(history_note)
        self.loot_history.hide()
        self.loot_history_toggle.toggled.connect(self._toggle_loot_history)
        loot_layout.addWidget(self.loot_history)
        layout.addWidget(loot_card)

        log_caption = QLabel("ACTIVITY")
        log_caption.setObjectName("section")
        layout.addWidget(log_caption)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setMaximumBlockCount(1200)
        self.log_view.setMinimumHeight(130)
        self.log_view.setAccessibleName("Automation activity log")
        layout.addWidget(self.log_view, 1)
        footer = QLabel("Closing this window leaves farming running independently.")
        footer.setObjectName("muted")
        footer.setWordWrap(True)
        layout.addWidget(footer)

    def _run(self, kind: str, operation: Callable):
        bridge = self._bridge

        def work():
            result, error = None, None
            try:
                result = operation()
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            try:
                bridge.finished.emit(kind, result, error)
            except RuntimeError:
                pass  # A closed window need not receive the completed result.

        # A command finishes normally even if the user closes the window mid-command.
        threading.Thread(target=work, name=f"nbr-manager-{kind}", daemon=kind != "command").start()

    @Slot()
    def refresh(self):
        if self._closed:
            return
        if self._poll_busy:
            self._poll_again = True
            return
        self._poll_busy = True
        revision = self._revision
        self._poll_revision = revision

        def poll():
            try:
                snapshot, snapshot_error = self.backend.snapshot(), None
            except Exception as exc:
                snapshot, snapshot_error = None, str(exc) or type(exc).__name__
            try:
                events, event_error = self.backend.read_events(), None
            except Exception as exc:
                events, event_error = [], str(exc)
            return revision, snapshot, snapshot_error, events, event_error

        self._run("poll", poll)

    @Slot()
    def refresh_connection(self):
        if self._closed or self._connection_busy:
            return
        self._connection_busy = True
        self._run("connection", self.backend.connection)

    @Slot(str, object, object)
    def _on_result(self, kind, result, error):
        if self._closed:
            return
        if kind == "stages":
            if error:
                self._stage_error = f"Could not load farming stages: {error}"
                self._append_log(self._stage_error)
            else:
                self.stage_combo.blockSignals(True)
                self.stage_combo.clear()
                for stage in result:
                    self.stage_combo.addItem(stage["name"], stage["id"])
                self.stage_combo.blockSignals(False)
                self._sync_stage()
            self._render_state()
        elif kind == "connection":
            self._connection_busy = False
            self.apply_connection(result if not error else {
                "available": False,
                "instance": self._connection.get("instance", "Unknown"),
                "detail": error,
            })
        elif kind == "poll":
            self._poll_busy = False
            snapshot_error = result[2] if result is not None else error
            if self._poll_revision != self._revision:
                self._poll_again = True
            elif snapshot_error:
                self._awaiting_verification = False
                self._snapshot_known = False
                message = f"Could not verify automation state: {snapshot_error}"
                if self._poll_error != message:
                    self._append_log(message)
                self._poll_error = message
                self._render_state()
            else:
                self.apply_snapshot(result[1])
            if result is not None:
                _, _, _, events, event_error = result
                if events:
                    self._append_log("\n".join(str(entry) for entry in events), timestamp=False)
                if event_error and event_error != self._last_event_error:
                    self._append_log(f"Could not read activity log: {event_error}")
                self._last_event_error = event_error or ""
            if self._poll_again:
                self._poll_again = False
                self.refresh()
        elif kind == "command":
            action = self._pending_action
            self._pending_action = None
            self._awaiting_verification = True
            self._revision += 1
            if error:
                self._command_error = f"{(action or 'Command').capitalize()} failed: {error}"
                self._append_log(self._command_error)
                self._sync_stage()
            else:
                self._append_log(str(result).strip() or f"{action.capitalize()} command completed; verifying state.")
            self._render_state()
            self.refresh()
        elif kind == "loot":
            self._loot_busy = False
            current_request = (self._loot_session_id, self._loot_offset)
            if result is not None and result[:2] == current_request:
                self.apply_loot_snapshot(result[2])
            elif error and self._loot_request == current_request:
                self.apply_loot_snapshot({"status": "Tracking Unavailable", "detail": error})
            if self._loot_again:
                self._loot_again = False
                self.refresh_loot()
        elif kind == "screenshot":
            if error:
                self.loot_detail_label.setText(f"Screenshot unavailable: {error}")
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(result)):
                self.loot_detail_label.setText("Screenshot could not be opened by the desktop.")

    @Slot(object)
    def apply_snapshot(self, snapshot):
        was_active = self._active
        previously_stopping = bool(self._snapshot.get("stop_requested"))
        self._snapshot = dict(snapshot)
        self._awaiting_verification = False
        self._snapshot_known = True
        self._poll_error = ""
        self._active = bool(snapshot.get("running") or snapshot.get("active_pids")
                            or snapshot.get("worker_running") or snapshot.get("supervisor_running"))
        if was_active and not self._active:
            if not (previously_stopping or snapshot.get("stop_requested") or self._pending_action in {"stop", "restart"}):
                self._exit_error = "Automation exited unexpectedly. Review the activity log before restarting."
                self._append_log(self._exit_error)
            self._update_runtime()
            self._started_at = None
        if self._active:
            started = snapshot.get("started_at")
            if isinstance(started, (int, float)) and started > 0:
                self._started_at = started
            self._exit_error = ""
        self._sync_stage()
        duration = snapshot.get("stamina_wait_minutes", 5)
        if self._pending_action != "stamina-duration":
            index = self.stamina_duration_combo.findData(duration)
            self.stamina_duration_combo.blockSignals(True)
            self.stamina_duration_combo.setCurrentIndex(max(0, index))
            self.stamina_duration_combo.blockSignals(False)
        self._render_state()
        self._update_runtime()
        self.refresh_loot()

    @Slot()
    def refresh_loot(self):
        if self._closed:
            return
        if self._loot_busy:
            self._loot_again = True
            return
        if not hasattr(self.backend, "loot_snapshot"):
            self.apply_loot_snapshot({"status": "Tracking Unavailable",
                                      "detail": "Loot tracking is unavailable in this backend."})
            return
        self._loot_busy = True
        session_id, offset, snapshot = self._loot_session_id, self._loot_offset, dict(self._snapshot)
        self._loot_request = (session_id, offset)
        self._run("loot", lambda: (session_id, offset, self.backend.loot_snapshot(
            snapshot, session_id, limit=self._loot_limit, offset=offset)))

    @Slot(int)
    def _loot_session_changed(self, index):
        self._loot_session_id = self.loot_session_combo.itemData(index)
        self._loot_offset = 0
        self.refresh_loot()

    def _change_loot_page(self, direction):
        self._loot_offset = max(0, self._loot_offset + direction * self._loot_limit)
        self.refresh_loot()

    @Slot(bool)
    def _toggle_loot_history(self, expanded):
        self.loot_history.setVisible(expanded)
        self.loot_history_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    @staticmethod
    def _loot_time(value):
        if value is None:
            return "Unknown"
        try:
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError, OSError, OverflowError):
            return str(value)

    @Slot(object)
    def apply_loot_snapshot(self, data):
        data = dict(data or {})
        self.loot_status_label.setText(str(data.get("status") or "Tracking Unavailable"))
        self.loot_detail_label.setText(str(data.get("detail") or ""))
        session = data.get("session") or {}
        stage_id = session.get("stage") or self._snapshot.get("stage")
        stage_index = self.stage_combo.findData(stage_id)
        stage_name = self.stage_combo.itemText(stage_index) if stage_index >= 0 else stage_id or "Unknown"
        confirmed = data.get("confirmed_quantities") or {}
        summary = (f"{session.get('account') or 'acc1'} · {stage_name}\n"
                   f"Completed runs observed: {data.get('completed_runs', 0)} · "
                   f"Recognized icons: {data.get('recognized_icons', 0)} · "
                   f"Unknown icons: {data.get('unknown_icons', 0)} · "
                   f"Ambiguous icons: {data.get('ambiguous_icons', 0)} · "
                   f"Recognition failures: {data.get('recognition_failures', 0)}")
        if confirmed:
            names = {item['item_id']: item.get('item_name') or item['item_id'] for item in data.get('items', [])}
            summary += "\nKnown confirmed acquired: " + "; ".join(
                f"{names.get(item_id, item_id)}: {quantity}" for item_id, quantity in confirmed.items())
            summary += " (unknown quantities excluded)"
        else:
            summary += "\nAcquired quantities: Unknown / not verified"
        self.loot_summary_label.setText(summary)
        total = int(data.get("completed_runs") or 0)
        displayed = len(data.get("runs") or [])
        self.loot_page_label.setText(f"{self._loot_offset + 1}–{self._loot_offset + displayed} of {total} runs"
                                     if displayed else "No runs on this page")
        self.loot_previous_button.setEnabled(self._loot_offset > 0)
        self.loot_next_button.setEnabled(self._loot_offset + self._loot_limit < total)
        self.loot_session_label.setText(
            f"Session: {session.get('session_id') or 'Unavailable'}\n"
            f"Process started: {self._loot_time(session.get('process_started'))} · "
            f"Tracking started: {self._loot_time(session.get('tracking_started'))}"
            + (f" · Ended: {self._loot_time(session['ended_at'])}" if session.get('ended_at') else ""))
        if data == self._loot_data:
            return
        self._loot_data = data
        self.loot_session_combo.blockSignals(True)
        self.loot_session_combo.clear()
        self.loot_session_combo.addItem("Current farming process", None)
        for historical in data.get("sessions", []):
            sid = historical.get("session_id")
            if not sid:
                continue
            text = (f"{historical.get('account') or 'acc1'} · {historical.get('stage') or 'Unknown'} · "
                    f"{self._loot_time(historical.get('tracking_started'))} · {sid}")
            self.loot_session_combo.addItem(text, sid)
        selected = self.loot_session_combo.findData(self._loot_session_id)
        if selected < 0 and self._loot_session_id:
            self.loot_session_combo.addItem(self._loot_session_id, self._loot_session_id)
            selected = self.loot_session_combo.count() - 1
        self.loot_session_combo.setCurrentIndex(max(0, selected))
        self.loot_session_combo.blockSignals(False)
        items = data.get("items") or []
        self.loot_item_table.setRowCount(len(items))
        for row, item in enumerate(items):
            acquired = item.get("confirmed_acquired")
            quantity = "Unknown" if acquired is None else str(acquired)
            if acquired is not None and item.get("unknown_quantities"):
                quantity += " (known only)"
            for column, value in enumerate((item.get("item_name") or item.get("item_id"),
                                             item.get("observed_icons", 0), quantity)):
                cell = QTableWidgetItem(str(value))
                if column == 0:
                    cell.setToolTip(" · ".join(str(value) for value in (item.get('record_type'),
                                        (f"{item['rarity']}★" if item.get('rarity') else None), item.get('element'), item.get('item_id')) if value))
                if column:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.loot_item_table.setItem(row, column, cell)
        expanded = {self.loot_run_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                    for i in range(self.loot_run_tree.topLevelItemCount())
                    if self.loot_run_tree.topLevelItem(i).isExpanded()}
        self.loot_run_tree.clear()
        for run in data.get("runs") or []:
            entry = QTreeWidgetItem([f"Run {run.get('run_sequence', '?')}",
                                     self._loot_time(run.get("completed_at")), str(run.get("status") or "Unknown")])
            entry.setData(0, Qt.ItemDataRole.UserRole, run.get("completion_id"))
            entry.setToolTip(1, self._loot_time(run.get("completed_at")))
            self.loot_run_tree.addTopLevelItem(entry)
            QTreeWidgetItem(entry, ["Completion ID", str(run.get("completion_id") or "Unknown"), ""])
            for reward in run.get("rewards") or []:
                identity = reward.get("identity_status", "unknown")
                name = (reward.get("item_name") or reward.get("item_id") or "Unknown reward")
                if identity != "matched":
                    name = f"{name} ({identity}; unconfirmed identity)"
                scope = reward.get("reward_scope")
                acquired = reward.get("quantity") if reward.get("quantity_status") == "verified" and identity == "matched" and scope == "acquired" else None
                if not isinstance(acquired, int) or isinstance(acquired, bool) or acquired < 0:
                    acquired = None
                observation = "1 observed icon" if scope in (None, "acquired") else f"Excluded scope: {scope}"
                child = QTreeWidgetItem(entry, [str(name), observation, "Unknown" if acquired is None else f"{acquired} confirmed acquired"])
                child.setToolTip(0, str(reward.get("reason") or ""))
                child.setToolTip(2, str(reward.get("quantity_reason") or ""))
            if run.get("unknown_icons") or run.get("ambiguous_icons"):
                QTreeWidgetItem(entry, ["Uncertain rewards", f"{run.get('unknown_icons', 0)} unknown", f"{run.get('ambiguous_icons', 0)} ambiguous"])
            for warning in run.get("warnings") or []:
                warning_row = QTreeWidgetItem(entry, ["Warning", str(warning), ""])
                warning_row.setToolTip(1, str(warning))
            if run.get("screenshot_path"):
                screenshot = QTreeWidgetItem(entry, ["Open retained screenshot", str(run["screenshot_path"]), "Double-click"])
                screenshot.setData(0, Qt.ItemDataRole.UserRole, {"screenshot_path": run["screenshot_path"]})
                screenshot.setToolTip(1, str(run["screenshot_path"]))
            entry.setExpanded(run.get("completion_id") in expanded)

    @Slot(QTreeWidgetItem, int)
    def _open_loot_screenshot(self, item, column=0):
        value = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(value, dict) or not value.get("screenshot_path"):
            return
        def resolve():
            path = Path(value["screenshot_path"])
            if not path.is_absolute() and hasattr(self.backend, "root"):
                path = self.backend.root / path
            if not path.is_file():
                raise FileNotFoundError("The retained file is no longer available.")
            return str(path.resolve())
        self._run("screenshot", resolve)

    @Slot(object)
    def apply_connection(self, connection):
        connection = dict(connection)
        before = self._connection
        self._connection = connection
        available = bool(connection.get("available"))
        self.connection_label.setText("● Connected" if available else "● Unavailable")
        self.connection_label.setStyleSheet(f"color: {'#5adea5' if available else '#ff9aa2'};")
        self.instance_label.setText(f"BlueStacks instance: {connection.get('instance') or 'Unknown'}")
        detail = str(connection.get("detail") or "")
        self.connection_label.setToolTip(detail)
        self.instance_label.setToolTip(detail)
        if before != connection:
            self._append_log(f"Connection {'verified' if available else 'unavailable'}: {detail or connection.get('instance', 'acc1')}")
        self._render_state()

    def _sync_stage(self):
        stage_id = self._desired_stage
        if self._active:
            stage_id = (self._snapshot.get("supervisor", {}).get("stage")
                        or self._snapshot.get("stage") or self._snapshot.get("selected_stage"))
        index = self.stage_combo.findData(stage_id)
        if index >= 0 and self._pending_action != "select":
            self.stage_combo.blockSignals(True)
            self.stage_combo.setCurrentIndex(index)
            self.stage_combo.blockSignals(False)
        name = self.stage_combo.itemText(index) if index >= 0 else "checking…"
        if stage_id == "universal":
            mission = self._snapshot.get("farm", {}).get("mission_name") if self._active else None
            self.current_stage_label.setText(f"Current mission: {mission or 'not identified'} · Universal")
        else:
            self.current_stage_label.setText(f"Current stage: {name}")

    def _render_state(self):
        snapshot = self._snapshot
        if self._poll_error:
            state = "ERROR"
        elif not self._snapshot_known:
            state = "STOPPED"
        elif self._active and snapshot.get("stop_requested"):
            state = "STOPPING"
        elif self._active and snapshot.get("worker_running"):
            state = "RUNNING"
        elif self._active:
            state = "STARTING"
        elif self._command_error or self._exit_error or self._stage_error or (
            self._connection and not self._connection.get("available")
        ):
            state = "ERROR"
        else:
            state = "STOPPED"
        if state != self._state:
            self._append_log(f"Automation status: {state}")
        self._state = state
        self.status_label.setText(f"● {state}" if self._snapshot_known or self._poll_error else "Checking process…")
        self.status_label.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {self.COLORS[state]};")

        connection_error = ""
        if self._connection and not self._connection.get("available"):
            connection_error = f"Connection unavailable: {self._connection.get('detail') or 'Could not verify acc1 device identity.'}"
        errors = [self._poll_error, self._command_error, self._exit_error, self._stage_error, connection_error]
        self.error_label.setText("\n".join(error for error in errors if error))
        self.error_label.setVisible(any(errors))

        if self._pending_action:
            details = {
                "start": "Start requested; waiting for the controller and verifying processes…",
                "stop": "Stop requested; waiting for the controller to finish safely…",
                "select": "Saving the selected stage…",
                "restart": "Full Restart: safely stopping old processes, then revalidating and relaunching…",
                "end-stamina-wait": "Interrupting stamina wait; the controller will recheck stamina before farming…",
                "stamina-duration": "Saving the duration for subsequent stamina waits…",
            }[self._pending_action]
        elif self._awaiting_verification:
            details = "Command completed; verifying current processes and selected stage…"
        elif not self._snapshot_known:
            details = "Waiting for a verified automation state."
        elif state == "STOPPING":
            details = "The controller is stopping. A bounded game operation may need to finish."
        elif state == "STARTING":
            details = "Controller process detected; waiting for its farming worker."
        elif self._active:
            details = str(snapshot.get("farm", {}).get("blocked") or "Farming is running independently for acc1.")
        else:
            details = "No active acc1 farming processes."
            if self._connection and not self._connection.get("available"):
                details += " Start will let the controller retry the BlueStacks connection safely."
        self.detail_label.setText(details)
        self._update_stamina()
        self._update_controls()

    def _update_controls(self):
        idle = self._pending_action is None and not self._awaiting_verification
        ready = self._snapshot_known and not self._active and idle
        self.start_button.setEnabled(bool(ready and self.stage_combo.currentData() and not self._stage_error))
        self.stop_button.setEnabled(bool(self._active and idle and not self._snapshot.get("stop_requested")))
        self.stage_combo.setEnabled(bool(ready and self.stage_combo.count()))
        self.restart_button.setEnabled(bool(idle and not self._closed and not self._stage_error))
        self.stamina_duration_combo.setEnabled(bool(idle and self._snapshot_known))
        self.end_stamina_wait_button.setEnabled(bool(idle and self._stamina_status() == "WAITING"
                                                   and self._snapshot.get("stamina_wait_interruptible")
                                                   and not self._snapshot.get("farm", {}).get("pending")
                                                   and not self._snapshot.get("stop_requested")))
        self.end_stamina_wait_button.setToolTip(
            "Interrupt the controller's wait and recheck game state and stamina. Insufficient stamina starts another wait."
            if self._snapshot.get("stamina_wait_interruptible") else
            "This worker predates interruptible waits. Use Full Restart to load the updated controller before ending a wait.")

    def _stamina_status(self):
        if (not self._snapshot_known or self._poll_error or not self._active
                or not self._snapshot.get("worker_running")):
            return "UNKNOWN"
        status = self._snapshot.get("stamina_status", "UNKNOWN")
        return status if status in {"WAITING", "READY", "UNKNOWN"} else "UNKNOWN"

    def _update_stamina(self):
        status = self._stamina_status()
        self.stamina_status_label.setText(status)
        deadline = self._snapshot.get("farm", {}).get("stamina_until")
        if (status == "WAITING" and isinstance(deadline, (int, float))
                and not isinstance(deadline, bool) and math.isfinite(deadline)):
            remaining = max(0, math.ceil(deadline - time.time()))
            minutes, seconds = divmod(remaining, 60)
            self.stamina_remaining_label.setText(f"Remaining: {minutes:02d}:{seconds:02d}")
        else:
            self.stamina_remaining_label.setText("Remaining: —")

    @Slot()
    def restart_automation(self):
        if self.restart_button.isEnabled():
            # An active or unavailable snapshot leaves stage recovery to the
            # controller's persisted selection instead of a guessed UI value.
            stage = self.stage_combo.currentData() if self._snapshot_known and not self._active else None
            self._command("restart", stage)

    @Slot()
    def end_stamina_wait(self):
        if self.end_stamina_wait_button.isEnabled():
            self._command("end-stamina-wait")

    @Slot(int)
    def _stamina_duration_changed(self, index):
        if index >= 0 and self.stamina_duration_combo.isEnabled():
            self._command("stamina-duration", self.stamina_duration_combo.itemData(index))

    @Slot()
    def start_automation(self):
        if self.start_button.isEnabled():
            self._command("start", self.stage_combo.currentData())

    @Slot()
    def stop_automation(self):
        if self.stop_button.isEnabled():
            self._command("stop")

    @Slot(int)
    def _stage_changed(self, index):
        if index < 0 or not self.stage_combo.isEnabled():
            return
        stage = self.stage_combo.itemData(index)
        self._desired_stage = stage
        if stage != self._snapshot.get("selected_stage"):
            self._command("select", stage)
        else:
            self._sync_stage()

    def _command(self, action, stage=None):
        if self._pending_action or self._closed:
            return
        self._pending_action = action
        self._revision += 1
        self._command_error = ""
        self._exit_error = ""
        selection = f"{stage} minutes" if action == "stamina-duration" else self.stage_combo.currentText()
        self._append_log(f"Requested {action}" + (f": {selection}" if stage else "."))
        self._render_state()
        self._run("command", lambda: self.backend.command(action, stage))

    @Slot()
    def _update_runtime(self):
        self._update_stamina()
        if self._started_at:
            self._elapsed_seconds = max(0, int(time.time() - self._started_at))
        if self._active and not self._started_at:
            self.runtime_label.setText("—")
            return
        hours, remainder = divmod(self._elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.runtime_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _append_log(self, message, timestamp=True):
        if not message:
            return
        if timestamp:
            message = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
        self.log_view.appendPlainText(message)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event: QCloseEvent):
        self._closed = True
        self.poll_timer.stop()
        self.connection_timer.stop()
        self.runtime_timer.stop()
        event.accept()


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("NBR Automation Manager")
    application.setApplicationVersion("1.1")
    try:
        window = ManagerWindow()
        window.show()
        return application.exec()
    except Exception as exc:
        QMessageBox.critical(None, "NBR Automation Manager", f"The application could not start.\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
