"""NBR Automation Manager — 1.23."""

from __future__ import annotations

import sys
import math
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSettings, QTimer, Signal, Slot, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
    QHeaderView, QScrollArea, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QDialog, QDialogButtonBox, QMenuBar,
    QLineEdit, QCheckBox, QFormLayout,
)


class _Results(QObject):
    finished = Signal(str, object, object)


class ManagerWindow(QMainWindow):
    """The backend is injectable so GUI tests never need a live game or controller."""

    COLORS = {
        "STOPPED": "#8994a7", "STARTING": "#f5c764", "RUNNING": "#5adea5",
        "STOPPING": "#f5c764", "ERROR": "#ff858d",
    }

    def __init__(self, backend=None, poll_interval_ms=2000, auto_poll=True,
                 settings=None, appearance=None):
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
        self._stop_pending = False
        self._awaiting_verification = False
        self._revision = 0
        self._poll_busy = False
        self._poll_again = False
        self._connection_busy = False
        self._connection_revision = 0
        self._instances_supported = callable(getattr(backend, "instances", None))
        self._instances_busy = False
        self._instances_again = False
        self._instance_command_busy = False
        self._instances_known = False
        self._instances_data = {}
        self._instance_error = ""
        self._instance_prefer_saved = False
        self._manual_dialog = None
        self._command_error = ""
        self._poll_error = ""
        self._exit_error = ""
        self._stage_error = ""
        self._started_at = None
        self._runtime_origin = None
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
        self._settings = settings if settings is not None else QSettings("NBR", "AutomationManager")
        self.show_activity_log = self._settings.value("show_activity_log", False, type=bool)
        self.show_recognition_details = self._settings.value("show_recognition_details", False, type=bool)
        self._appearance_dialog = None
        self._build_appearance_menu()
        from manager_theme import ManagerPresentation
        self._presentation = ManagerPresentation(self)
        self.appearance = "original"
        selected = appearance or str(self._settings.value("appearance", "original"))
        self.set_appearance(selected if selected in {"original", "retro98"} else "original",
                            persist=False)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(max(50, int(poll_interval_ms)))
        self.poll_timer.timeout.connect(self.refresh)
        self.connection_timer = QTimer(self)
        self.connection_timer.setInterval(15000)
        self.connection_timer.timeout.connect(self.refresh_connection)
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.runtime_timer.setSingleShot(True)
        self.runtime_timer.setInterval(1000)
        self.runtime_timer.timeout.connect(self._update_runtime)
        self.runtime_timer.start()
        self._run("stages", self.backend.stages)
        if self._instances_supported:
            self.refresh_instances()
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
        self.resize(760, 720)
        self.setMinimumSize(560, 610)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #10151e; color: #e7edf7; }
            QLabel { background: transparent; }
            QLabel#muted { color: #9caac0; }
            QLabel#title { font-size: 23px; font-weight: 650; }
            QLabel#section { color: #9caac0; font-size: 11px; font-weight: 650; }
            QFrame#card { background: #19212f; border: 1px solid #293448; border-radius: 12px; }
            QComboBox, QLineEdit { background: #202b3d; border: 1px solid #40516c; border-radius: 7px;
                        padding: 11px; min-height: 20px; }
            QComboBox:focus { border: 1px solid #84b5ff; }
            QComboBox:disabled { color: #a3aec0; background: #1a2433; }
            QComboBox QAbstractItemView { background: #202b3d; color: #e7edf7;
                                         selection-background-color: #355687; }
            QPushButton { border-radius: 8px; border: 1px solid #415470;
                          background: #26354b; padding: 11px; font-size: 15px; font-weight: 650; }
            QPushButton:hover { background: #344866; }
            QPushButton#retroNav:checked { background: #355687; border-color: #84b5ff; }
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
        version = QLabel("v1.23")
        version.setObjectName("muted")
        header.addStretch()
        header.addWidget(version)
        layout.addLayout(header)

        account_card = QFrame()
        account_card.setObjectName("card")
        account_layout = QVBoxLayout(account_card)
        account_layout.setContentsMargins(16, 13, 16, 13)
        account_line = QHBoxLayout()
        account = self.target_name_label = QLabel("Instance")
        account.setTextFormat(Qt.TextFormat.PlainText)
        account.setStyleSheet("font-weight: 650;")
        self.connection_label = QLabel("Checking connection…")
        self.connection_label.setStyleSheet("color: #9caac0;")
        account_line.addWidget(account)
        account_line.addStretch()
        account_line.addWidget(self.connection_label)
        account_layout.addLayout(account_line)
        self.instance_label = QLabel("BlueStacks instance: checking…")
        self.instance_label.setTextFormat(Qt.TextFormat.PlainText)
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
        runtime_caption = QLabel("ELAPSED")
        runtime_caption.setObjectName("section")
        self.runtime_label = QLabel("00:00:00")
        self.runtime_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.runtime_label.setStyleSheet("font-size: 21px; font-weight: 600;")
        runtime_group.addWidget(runtime_caption)
        runtime_group.addWidget(self.runtime_label)
        status_line.addLayout(runtime_group)
        layout.addLayout(status_line)

        self.stage_combo = QComboBox(central)
        self.stage_combo.setAccessibleName("Automation mode")
        self.stage_combo.hide()
        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("start")
        self.start_button.setMinimumHeight(51)
        self.start_button.setToolTip("Open a mission’s Start Mission screen, then start farming.")
        self.start_button.clicked.connect(self.start_automation)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stop")
        self.stop_button.setMinimumHeight(51)
        self.stop_button.clicked.connect(self.stop_automation)
        self.restart_button = QPushButton("Restart")
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
        self.end_stamina_wait_button = QPushButton("End wait")
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
        from manager_loot import LootGrid
        self.loot_grid = LootGrid()
        loot_layout.addWidget(self.loot_grid)
        self.loot_summary_label = QLabel("Waiting for persisted loot records.")
        self.loot_summary_label.setWordWrap(True)
        self.loot_summary_label.setTextFormat(Qt.TextFormat.PlainText)
        loot_layout.addWidget(self.loot_summary_label)
        self.loot_detail_label = QLabel()
        self.loot_detail_label.setObjectName("muted")
        self.loot_detail_label.setWordWrap(True)
        self.loot_detail_label.setTextFormat(Qt.TextFormat.PlainText)
        loot_layout.addWidget(self.loot_detail_label)
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
        self.loot_item_table.setHorizontalHeaderLabels(["Item / Character", "Observed Icons", "Drops"])
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
        self.loot_run_tree.setToolTip("Expand runs for details. Double-click screenshots to open.")
        loot_layout.addWidget(self.loot_history)
        layout.addWidget(loot_card)

        self.log_caption = QLabel("ACTIVITY LOG")
        self.log_caption.setObjectName("section")
        layout.addWidget(self.log_caption)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setMaximumBlockCount(1200)
        self.log_view.setMinimumHeight(130)
        self.log_view.setAccessibleName("Automation activity log")
        layout.addWidget(self.log_view, 1)
        footer = QLabel("Closing leaves farming running.")
        self.footer_label = footer
        footer.setObjectName("muted")
        footer.setWordWrap(True)
        layout.addWidget(footer)

    def _build_appearance_menu(self):
        shell = QWidget()
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        menu = QMenuBar()
        menu.addMenu("&File").addAction("&Close", self.close)
        settings_menu = menu.addMenu("&Settings")
        settings_menu.addAction("&Appearance…", self.show_appearance_settings)
        settings_menu.addSeparator()
        self.activity_log_action = settings_menu.addAction("Activity &log")
        self.recognition_details_action = settings_menu.addAction("&Recognition details")
        for action, key in ((self.activity_log_action, "show_activity_log"),
                            (self.recognition_details_action, "show_recognition_details")):
            action.setCheckable(True)
            action.setChecked(getattr(self, key))
            action.toggled.connect(lambda checked, key=key: self._set_display_setting(key, checked))
        self._menu_version = QLabel("v1.23  ")
        menu.setCornerWidget(self._menu_version)
        layout.addWidget(menu)
        self.setMenuWidget(shell)

    def show_appearance_settings(self):
        if self._appearance_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Appearance — NBR Automation Manager")
            dialog.setModal(True)
            layout = QVBoxLayout(dialog)
            self.appearance_combo = QComboBox()
            self.appearance_combo.setAccessibleName("Application appearance")
            self.appearance_combo.addItem("Original", "original")
            self.appearance_combo.addItem("Retro 98", "retro98")
            layout.addWidget(self.appearance_combo)
            self._appearance_save_label = QLabel()
            self._appearance_save_label.setWordWrap(True)
            self._appearance_save_label.setTextFormat(Qt.TextFormat.PlainText)
            self._appearance_save_label.hide()
            layout.addWidget(self._appearance_save_label)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            self.appearance_combo.currentIndexChanged.connect(
                lambda index: self.set_appearance(self.appearance_combo.itemData(index)))
            self._appearance_dialog = dialog
        self.appearance_combo.blockSignals(True)
        self.appearance_combo.setCurrentIndex(self.appearance_combo.findData(self.appearance))
        self.appearance_combo.blockSignals(False)
        self._appearance_dialog.open()

    def set_appearance(self, appearance, *, persist=True):
        if appearance not in {"original", "retro98"}:
            raise ValueError("Unknown appearance")
        self._presentation.apply(appearance)
        self.appearance = appearance
        self._menu_version.setVisible(appearance == "retro98")
        self._style_status()
        self._style_connection()
        self._update_detail_visibility()
        self._presentation.update_detail_visibility(self.show_activity_log, self.show_recognition_details)
        self._update_loot_tiles()
        if self._appearance_dialog is not None:
            self.appearance_combo.blockSignals(True)
            self.appearance_combo.setCurrentIndex(self.appearance_combo.findData(appearance))
            self.appearance_combo.blockSignals(False)
        if persist:
            self._settings.setValue("appearance", appearance)
            self._settings.sync()
            if self._appearance_dialog is not None:
                failed = self._settings.status() != QSettings.Status.NoError
                self._appearance_save_label.setText("Could not save appearance preference.")
                self._appearance_save_label.setVisible(failed)

    def _set_display_setting(self, key, visible):
        setattr(self, key, bool(visible))
        self._presentation.update_detail_visibility(self.show_activity_log, self.show_recognition_details)
        self._settings.setValue(key, bool(visible))
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError:
            self._append_log("Could not save display preference.")

    def _style_status(self):
        self._presentation.monitor_label.setText(
            f"● {self._state} · {self._dashboard_instance_name()}" if self._snapshot_known or self._poll_error else "Checking process…")
        self._presentation.monitor_label.setStyleSheet(f"color: {self.COLORS[self._state]}; font-weight: bold;")
        if self.appearance == "retro98":
            backgrounds = {"RUNNING": "#d6e9c1", "ERROR": "#f0d7c8",
                           "STARTING": "#f2e3b1", "STOPPING": "#f2e3b1"}
            background = backgrounds.get(self._state, "#e0dfd3")
            size = 28 if self._snapshot_known or self._poll_error else 18
            self.status_label.setStyleSheet(
                f"font: bold {size}px 'Lucida Console'; color: {self.COLORS[self._state]};"
                f"background: {background}; border: 1px inset #d3d4bf; padding: 8px;")
            self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self.status_label.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {self.COLORS[self._state]};")
            self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

    def _style_connection(self):
        available = bool(self._connection.get("available"))
        if self.appearance == "retro98":
            color = "#145a28" if available else "#a02222"
            background = "#d6e9c1" if available else "#eee1cf"
            self.connection_label.setStyleSheet(f"color: {color}; background: {background}; padding: 4px 9px;")
        elif self._connection:
            self.connection_label.setStyleSheet(f"color: {'#5adea5' if available else '#ff9aa2'};")
        else:
            self.connection_label.setStyleSheet("color: #9caac0;")

    def _update_detail_visibility(self):
        redundant = {"No active farming processes.", "Farming is running independently."}
        self.detail_label.setVisible(self.detail_label.text() not in redundant)

    def _build_instance_page(self):
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        contents = QWidget()
        page.setWidget(contents)
        layout = QVBoxLayout(contents)
        layout.setContentsMargins(24, 22, 24, 20)
        title = QLabel("Instance")
        title.setObjectName("title")
        layout.addWidget(title)
        description = QLabel("Choose the BlueStacks instance to use for automation.")
        description.setWordWrap(True)
        description.setObjectName("muted")
        layout.addWidget(description)
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(QLabel("Instance Selection"))
        self.instance_combo = QComboBox()
        self.instance_combo.setAccessibleName("BlueStacks instance")
        self.instance_combo.addItem("Checking installed instances…", None)
        self.instance_combo.currentIndexChanged.connect(self._instance_preview_changed)
        card_layout.addWidget(self.instance_combo)
        self.instance_message_label = QLabel("Checking installed instances…")
        self.instance_message_label.setWordWrap(True)
        self.instance_message_label.setTextFormat(Qt.TextFormat.PlainText)
        card_layout.addWidget(self.instance_message_label)
        buttons = QHBoxLayout()
        self.refresh_instances_button = QPushButton("Refresh Instances")
        self.refresh_instances_button.clicked.connect(self.refresh_instances)
        self.apply_instance_button = QPushButton("Apply Selection")
        self.apply_instance_button.clicked.connect(self.apply_instance_selection)
        self.manual_instance_button = QPushButton("Manual Setup…")
        self.manual_instance_button.clicked.connect(self.show_manual_instance_setup)
        for button in (self.refresh_instances_button, self.apply_instance_button, self.manual_instance_button):
            buttons.addWidget(button)
        card_layout.addLayout(buttons)
        layout.addWidget(card)
        details = QFrame()
        details.setObjectName("card")
        detail_layout = QVBoxLayout(details)
        detail_layout.addWidget(QLabel("Instance Details"))
        form = QFormLayout()
        self.instance_detail_labels = {}
        for field, caption in (("display_name", "Display name"), ("instance_id", "Internal identifier"),
                               ("status", "Instance status"), ("endpoint", "ADB connection address"),
                               ("connection_status", "Connection status")):
            label = QLabel("—")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(caption, label)
            self.instance_detail_labels[field] = label
        detail_layout.addLayout(form)
        layout.addWidget(details)
        note = QLabel("Instance changes are available after farming has safely stopped.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return page

    @Slot()
    def refresh_instances(self):
        if (self._closed or not self._instances_supported or self._instances_busy
                or self._instance_command_busy):
            return
        self._instances_busy = True
        self._instance_error = ""
        self._update_instance_controls()
        self._update_controls()
        self._run("instances", self.backend.instances)

    def apply_instances(self, data):
        self._instances_data = dict(data)
        self._instances_known = True
        records = self._instances_data.get("instances") or []
        old_key = self.instance_combo.currentData()
        selected = self._instances_data.get("selected_key")
        active = self._instances_data.get("active_key") if self._active else None
        keys = {item.get("key") for item in records}
        preferred = active or (selected if self._instance_prefer_saved else
                               old_key if old_key in keys else selected)
        self._instance_prefer_saved = False
        if preferred not in keys:
            preferred = records[0].get("key") if len(records) == 1 else None
        self.instance_combo.blockSignals(True)
        self.instance_combo.clear()
        if not records:
            self.instance_combo.addItem("No BlueStacks instances found", None)
        else:
            if preferred is None:
                self.instance_combo.addItem("Choose an instance…", None)
            for item in records:
                name = item.get("display_name") or item.get("instance_id") or "Unknown instance"
                status = str(item.get("status") or "unavailable").capitalize()
                self.instance_combo.addItem(f"{name} · {status}", item.get("key"))
                self.instance_combo.setItemData(self.instance_combo.count() - 1,
                                               item.get("detail") or "", Qt.ItemDataRole.ToolTipRole)
        index = self.instance_combo.findData(preferred)
        self.instance_combo.setCurrentIndex(max(0, index))
        self.instance_combo.blockSignals(False)
        self._instance_preview_changed()
        self._update_target_display()
        self._update_controls()

    def _instance_preview_changed(self, *args):
        key = self.instance_combo.currentData()
        record = next((item for item in self._instances_data.get("instances", [])
                       if item.get("key") == key), {})
        for field, label in self.instance_detail_labels.items():
            value = str(record.get(field) or "—")
            label.setText(value.capitalize() if field in {"status", "connection_status"} else value)
            label.setToolTip(str(record.get("detail") or ""))
        self._update_instance_controls()

    def _instance_selection_locked(self):
        return bool(self._closed or not self._snapshot_known or self._poll_error or self._active
                    or self._pending_action or self._stop_pending or self._awaiting_verification
                    or self._instances_busy or self._instance_command_busy
                    or self._instances_data.get("locked"))

    def _update_instance_controls(self):
        if not hasattr(self, "instance_combo"):
            return
        locked = self._instance_selection_locked()
        supported = self._instances_supported
        self.instance_combo.setEnabled(bool(supported and not locked and self._instances_known
                                           and self._instances_data.get("instances")))
        self.apply_instance_button.setEnabled(bool(supported and self._instances_known and not locked
            and self.instance_combo.currentData() is not None
            and (self.instance_combo.currentData() != self._instances_data.get("selected_key")
                 or self._instances_data.get("selection_required"))))
        self.manual_instance_button.setEnabled(bool(supported and not locked))
        self.refresh_instances_button.setEnabled(bool(supported and not self._closed
            and not self._instances_busy and not self._instance_command_busy))
        if self._manual_dialog is not None:
            for control in (self.manual_name_edit, self.manual_advanced_check,
                            self.manual_endpoint_edit, self.manual_apply_button):
                control.setEnabled(not locked)
        if self._instance_error:
            message = self._instance_error
        elif self._instance_command_busy:
            message = "Verifying the instance and saving your selection…"
        elif self._instances_busy:
            message = "Checking installed instances…"
        elif not supported:
            message = "Instance discovery is unavailable in this backend."
        elif self._active or self._instances_data.get("locked"):
            message = "Farming is active. Stop farming safely before changing the instance."
        elif not self._snapshot_known or self._poll_error:
            message = "Waiting for a verified automation state before changing the instance."
        elif self._pending_action or self._stop_pending or self._awaiting_verification:
            message = "Waiting for the controller to finish before changing the instance."
        elif self.instance_combo.currentData() is None and self._instances_data.get("instances"):
            message = "Choose an instance, then apply your selection."
        else:
            message = str(self._instances_data.get("detail") or
                          "Apply your selection to verify the connection before using it.")
        self.instance_message_label.setText(message)

    @Slot()
    def apply_instance_selection(self):
        key = self.instance_combo.currentData()
        if self._instance_selection_locked() or key is None or not self._instances_supported:
            return
        if key == self._instances_data.get("selected_key") and not self._instances_data.get("selection_required"):
            return
        self._change_instance(lambda: self.backend.apply_instance(key))

    def _change_instance(self, operation):
        self._instance_command_busy = True
        self._instance_error = ""
        self._connection_revision += 1
        self._update_controls()
        self._run("instance-selection", operation)

    @Slot()
    def show_manual_instance_setup(self):
        if self._instance_selection_locked() or not self._instances_supported:
            return
        if self._manual_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Manual Instance Setup")
            layout = QVBoxLayout(dialog)
            instruction = QLabel("Enter a BlueStacks display name or internal identifier. The target must be verified before it can be saved.")
            instruction.setWordWrap(True)
            layout.addWidget(instruction)
            form = QFormLayout()
            self.manual_name_edit = QLineEdit()
            self.manual_name_edit.setAccessibleName("Instance name or identifier")
            form.addRow("Instance name or identifier", self.manual_name_edit)
            layout.addLayout(form)
            self.manual_advanced_check = QCheckBox("Advanced: enter an ADB endpoint")
            layout.addWidget(self.manual_advanced_check)
            self.manual_endpoint_edit = QLineEdit()
            self.manual_endpoint_edit.setAccessibleName("ADB endpoint")
            self.manual_endpoint_edit.setPlaceholderText("127.0.0.1:port")
            self.manual_endpoint_edit.hide()
            self.manual_advanced_check.toggled.connect(self.manual_endpoint_edit.setVisible)
            layout.addWidget(self.manual_endpoint_edit)
            self.manual_error_label = QLabel()
            self.manual_error_label.setWordWrap(True)
            self.manual_error_label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(self.manual_error_label)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            buttons.rejected.connect(dialog.reject)
            self.manual_apply_button = buttons.addButton("Verify and Apply", QDialogButtonBox.ButtonRole.ActionRole)
            self.manual_apply_button.clicked.connect(self._apply_manual_instance)
            layout.addWidget(buttons)
            self._manual_dialog = dialog
        self.manual_error_label.clear()
        self._update_instance_controls()
        self._manual_dialog.open()

    def _apply_manual_instance(self):
        if self._instance_selection_locked():
            return
        name = self.manual_name_edit.text().strip()
        endpoint = self.manual_endpoint_edit.text().strip() if self.manual_advanced_check.isChecked() else ""
        if not name:
            self.manual_error_label.setText("Enter an instance name or identifier.")
            return
        if self.manual_advanced_check.isChecked() and not endpoint:
            self.manual_error_label.setText("Enter an ADB endpoint or turn off the advanced option.")
            return
        self.manual_error_label.setText("Verifying instance…")
        self._change_instance(lambda: self.backend.manual_instance(name, endpoint))

    def _dashboard_instance(self):
        target = self._snapshot.get("instance")
        if self._active:
            # Never replace the active process's pinned target with a dropdown preview.
            return target if isinstance(target, dict) else {}
        if isinstance(target, dict) and target:
            return target
        selected = self._instances_data.get("selected_key")
        return next((item for item in self._instances_data.get("instances", [])
                     if item.get("key") == selected), target if isinstance(target, dict) else {})

    def _dashboard_instance_name(self):
        target = self._dashboard_instance()
        if self._active and self._instances_supported and not target:
            return "Active target unavailable"
        return str(target.get("display_name") or target.get("instance_id")
                   or self._connection.get("instance") or "Instance")

    def _update_target_display(self):
        self.target_name_label.setText(self._dashboard_instance_name())
        target = self._dashboard_instance()
        if self._active and self._instances_supported and not target:
            text = "Active process target: unavailable"
        elif target:
            pieces = [str(target.get("display_name") or target.get("instance_id") or "Unknown")]
            for field in ("instance_id", "endpoint"):
                value = target.get(field)
                if value and str(value) not in pieces:
                    pieces.append(str(value))
            text = "BlueStacks instance: " + " · ".join(pieces)
        else:
            text = f"BlueStacks instance: {self._connection.get('instance') or 'Choose an instance'}"
        self.instance_label.setText(text)
        if hasattr(self, "_presentation"):
            self._style_status()

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
        threading.Thread(target=work, name=f"nbr-manager-{kind}",
                         daemon=kind not in {"command", "stop-command", "instance-selection"}).start()

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
        revision = self._connection_revision
        self._connection_request_revision = revision
        self._run("connection", lambda: (revision, self.backend.connection()))

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
            if self._connection_request_revision != self._connection_revision:
                self.refresh_connection()
                return
            if not error and isinstance(result, tuple):
                revision, result = result
                if revision != self._connection_revision:
                    self.refresh_connection()
                    return
            self.apply_connection(result if not error else {
                "available": False,
                "instance": self._connection.get("instance", "Unknown"),
                "detail": error,
            })
        elif kind == "instances":
            self._instances_busy = False
            if error:
                self._instances_known = False
                self._instance_error = f"Could not discover BlueStacks instances: {error}"
                self._append_log(self._instance_error)
                self._update_controls()
            else:
                self.apply_instances(result)
            if self._instances_again:
                self._instances_again = False
                self.refresh_instances()
        elif kind == "instance-selection":
            self._instance_command_busy = False
            self._connection_revision += 1
            if error:
                self._instance_error = f"Instance selection failed: {error}"
                self._append_log(self._instance_error)
                if self._manual_dialog is not None:
                    self.manual_error_label.setText(self._instance_error)
                self._update_controls()
            else:
                if self._manual_dialog is not None:
                    self._manual_dialog.accept()
                self._append_log(str(result).strip() or "Instance verified and selection saved.")
                self._instances_known = False
                self._instance_prefer_saved = True
                self._awaiting_verification = True
                self._revision += 1
                self.refresh_instances()
                self.refresh_connection()
                self.refresh()
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
        elif kind in {"command", "stop-command"}:
            if kind == "stop-command":
                action = "stop"
                self._stop_pending = False
            else:
                action = self._pending_action or "command"
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
        if "instance_locked" in snapshot:
            self._instances_data["locked"] = bool(snapshot["instance_locked"])
        if was_active != self._active and self._instances_supported:
            if self._instances_busy:
                self._instances_again = True
            else:
                self.refresh_instances()
        self._update_target_display()
        if was_active and not self._active:
            if not (previously_stopping or snapshot.get("stop_requested") or self._stop_pending
                    or self._pending_action in {"stop", "restart"}):
                self._exit_error = "Automation exited unexpectedly. Review the activity log before restarting."
                self._append_log(self._exit_error)
            self._update_runtime()
            self._started_at = None
            self._runtime_origin = None
        if self._active:
            started = snapshot.get("started_at")
            if (isinstance(started, (int, float)) and not isinstance(started, bool)
                    and math.isfinite(started) and started > 0 and started != self._started_at):
                self._started_at = started
                # Restore an existing run once, then keep elapsed time independent
                # of wall-clock corrections and asynchronous status refreshes.
                self._runtime_origin = time.monotonic() - max(0.0, time.time() - started)
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
        self.loot_grid.setToolTip("\n".join(filter(None, (
            self.loot_status_label.text(), self.loot_detail_label.text()))))
        session = data.get("session") or {}
        summary = (f"{session.get('account') or 'acc1'}\n"
                   f"Completed runs observed: {data.get('completed_runs', 0)} · "
                   f"Recognized icons: {data.get('recognized_icons', 0)} · "
                   f"Unknown icons: {data.get('unknown_icons', 0)} · "
                   f"Ambiguous icons: {data.get('ambiguous_icons', 0)} · "
                   f"Recognition failures: {data.get('recognition_failures', 0)}")
        from manager_loot import acquired_count
        items = data.get('items') or []
        summary += "\nDrops: " + ("; ".join(
            f"{item.get('item_name') or item.get('item_id')}: {acquired_count(item)[0]}" for item in items)
            if items else "0")
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
        self._update_loot_tiles()
        self.loot_session_combo.blockSignals(True)
        self.loot_session_combo.clear()
        self.loot_session_combo.addItem("Current farming process", None)
        for historical in data.get("sessions", []):
            sid = historical.get("session_id")
            if not sid:
                continue
            text = (f"{historical.get('account') or 'acc1'} · "
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
            quantity = acquired_count(item)[0]
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
                acquired_icon = scope == "acquired" and reward.get("visual_state", "icon") == "icon"
                observation = "1 observed icon" if acquired_icon else f"Excluded scope: {scope}"
                child = QTreeWidgetItem(entry, [str(name), observation, "1 drop" if acquired_icon else "Excluded"])
                child.setToolTip(0, str(reward.get("reason") or ""))
                child.setToolTip(2, "Each acquired icon counts as one drop." if acquired_icon else "Not a visible acquired drop.")
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

    def _update_loot_tiles(self):
        tiles = list(self._loot_data.get('items') or [])
        unidentified = self._loot_data.get('unidentified_drops', 0)
        if type(unidentified) is int and unidentified > 0:
            tiles.append({'item_name': 'Unidentified', 'drop_count': unidentified})
        if self.appearance == 'retro98':
            from manager_loot import sort_loot_items
            tiles = sort_loot_items(tiles, self._presentation.loot_sort_combo.currentData())
        self.loot_grid.set_items(tiles)

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
        self._style_connection()
        self._update_target_display()
        detail = str(connection.get("detail") or "")
        self.connection_label.setToolTip(detail)
        self.instance_label.setToolTip(detail)
        if before != connection:
            self._append_log(f"Connection {'verified' if available else 'unavailable'}: {detail or connection.get('instance', 'Unknown')}")
        self._render_state()

    def _sync_stage(self):
        index = self.stage_combo.findData("universal")
        if index >= 0:
            self.stage_combo.blockSignals(True)
            self.stage_combo.setCurrentIndex(index)
            self.stage_combo.blockSignals(False)

    def _render_state(self):
        snapshot = self._snapshot
        self._presentation.update_completed_missions()
        if self._poll_error:
            state = "ERROR"
        elif not self._snapshot_known:
            state = "STOPPED"
        elif self._stop_pending or (self._active and snapshot.get("stop_requested")):
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
        self._style_status()

        connection_error = ""
        if self._connection and not self._connection.get("available"):
            connection_error = f"Connection unavailable: {self._connection.get('detail') or 'Could not verify the selected device identity.'}"
        errors = [self._poll_error, self._command_error, self._exit_error, self._stage_error, connection_error]
        self.error_label.setText("\n".join(error for error in errors if error))
        self.error_label.setVisible(any(errors))

        if self._stop_pending:
            details = "Stop requested; cancelling any queued launch and waiting for the controller to finish safely…"
        elif self._pending_action:
            details = {
                "start": "Start requested; waiting for the controller and verifying processes…",
                "stop": "Stop requested; waiting for the controller to finish safely…",
                "restart": "Full Restart: safely stopping old processes, then revalidating and relaunching…",
                "end-stamina-wait": "Interrupting stamina wait; the controller will recheck stamina before farming…",
                "stamina-duration": "Saving the duration for subsequent stamina waits…",
            }[self._pending_action]
        elif self._awaiting_verification:
            details = "Verifying controller…"
        elif not self._snapshot_known:
            details = "Waiting for a verified automation state."
        elif state == "STOPPING":
            details = "The controller is stopping. A bounded game operation may need to finish."
        elif state == "STARTING":
            details = "Controller process detected; waiting for its farming worker."
        elif self._active:
            details = str(snapshot.get("farm", {}).get("blocked") or "Farming is running independently.")
        else:
            details = "No active farming processes."
            if self._connection and not self._connection.get("available"):
                details += " Start will let the controller retry the BlueStacks connection safely."
        self.detail_label.setText(details)
        self._update_detail_visibility()
        self._update_stamina()
        self._update_controls()

    def _update_controls(self):
        idle = self._pending_action is None and not self._stop_pending and not self._awaiting_verification
        ready = self._snapshot_known and not self._active and idle
        instance_ready = (not self._instances_supported or (self._instances_known
                          and self._instances_data.get("selected_key")
                          and not self._instances_data.get("selection_required")
                          and not self._snapshot.get("instance_selection_required")))
        instance_idle = not self._instance_command_busy and not self._instances_busy
        self.start_button.setEnabled(bool(ready and instance_ready and instance_idle
                                          and self.stage_combo.currentData() and not self._stage_error))
        launch_pending = self._pending_action in {"start", "restart"}
        self.stop_button.setEnabled(bool(not self._closed and not self._stop_pending
                                         and (launch_pending or (self._active
                                              and not self._snapshot.get("stop_requested")))))
        self.stage_combo.setEnabled(bool(ready and self.stage_combo.count()))
        self.restart_button.setEnabled(bool(idle and not self._closed and not self._stage_error
                                            and instance_idle and (self._active or instance_ready)))
        self.stamina_duration_combo.setEnabled(bool(idle and self._snapshot_known))
        self.end_stamina_wait_button.setEnabled(bool(idle and self._stamina_status() == "WAITING"
                                                   and self._snapshot.get("stamina_wait_interruptible")
                                                   and not self._snapshot.get("farm", {}).get("pending")
                                                   and not self._snapshot.get("stop_requested")))
        self.end_stamina_wait_button.setToolTip(
            "Interrupt the controller's wait and recheck game state and stamina. Insufficient stamina starts another wait."
            if self._snapshot.get("stamina_wait_interruptible") else
            "This worker predates interruptible waits. Use Full Restart to load the updated controller before ending a wait.")
        self._update_instance_controls()

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
            seconds_left = max(0.0, deadline - time.time())
            remaining = math.ceil(seconds_left)
            minutes, seconds = divmod(remaining, 60)
            self.stamina_remaining_label.setText(f"Remaining: {minutes:02d}:{seconds:02d}")
            if remaining:
                return seconds_left - remaining + 1
        else:
            self.stamina_remaining_label.setText("Remaining: —")
        return 1.0

    @Slot()
    def restart_automation(self):
        if self.restart_button.isEnabled():
            # An unavailable snapshot leaves recovery to the controller.
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

    def _command(self, action, stage=None):
        if self._closed or self._stop_pending or (self._pending_action and action != "stop"):
            return
        try:
            prepare = getattr(self.backend, "prepare_command", None)
            operation = prepare(action, stage) if prepare else lambda: self.backend.command(action, stage)
        except Exception as exc:
            self._command_error = f"Could not prepare {action}: {exc}"
            self._append_log(self._command_error)
            self._render_state()
            return
        if action == "stop":
            self._stop_pending = True
        else:
            self._pending_action = action
        self._revision += 1
        self._command_error = ""
        self._exit_error = ""
        selection = f"{stage} minutes" if action == "stamina-duration" else self.stage_combo.currentText()
        self._append_log(f"Requested {action}" + (f": {selection}" if stage else "."))
        self._render_state()
        self._run("stop-command" if action == "stop" else "command", operation)

    @Slot()
    def _update_runtime(self):
        next_tick = self._update_stamina()
        if self._runtime_origin is not None:
            elapsed = max(0.0, time.monotonic() - self._runtime_origin)
            self._elapsed_seconds = int(elapsed)
            next_tick = min(next_tick, self._elapsed_seconds + 1 - elapsed)
        if self._active and self._runtime_origin is None:
            self.runtime_label.setText("—")
        else:
            hours, remainder = divmod(self._elapsed_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.runtime_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        if not self._closed:
            self.runtime_timer.start(max(1, math.ceil(next_tick * 1000)))

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_presentation"):
            QTimer.singleShot(0, self._presentation.update_monitor_visibility)


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("NBR Automation Manager")
    application.setApplicationVersion("1.23")
    try:
        window = ManagerWindow()
        window.show()
        return application.exec()
    except Exception as exc:
        QMessageBox.critical(None, "NBR Automation Manager", f"The application could not start.\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
