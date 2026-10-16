"""Reversible Qt presentations; controls and controller ownership stay in ManagerWindow."""

from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, QSize, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from manager_art import pixel_icon


RETRO_COLORS = {
    "STOPPED": "#4f5868", "STARTING": "#795600", "RUNNING": "#145a28",
    "STOPPING": "#795600", "ERROR": "#a02222",
}

RETRO_STYLE = """
    QMainWindow, QWidget { background: #c8c7bd; color: #10185d;
        font-family: Tahoma; font-size: 13px; }
    QLabel { background: transparent; }
    QLabel#title { font-size: 20px; font-weight: bold; }
    QWidget#retroSlot { background: transparent; }
    QLabel#muted { color: #425075; }
    QFrame#retroPanel, QFrame#card { background: #f0ecd7; border: 2px solid;
        border-top-color: #ffffff; border-left-color: #ffffff;
        border-right-color: #77796f; border-bottom-color: #77796f; }
    QWidget#retroBody, QWidget#retroContents { background: #d5d3c6; }
    QLabel#panelHeading { background: #c8c7bd; font-weight: bold; font-size: 14px; padding: 3px 7px;
        border-bottom: 1px solid #8a8b80; }
    QMenuBar { background: #c8c7bd; padding: 2px; }
    QMenuBar::item { padding: 3px 12px; background: transparent; }
    QMenuBar::item:selected, QMenu::item:selected { color: white; background: #003caa; }
    QMenu { background: #c8c7bd; border: 2px outset #eeeeea; }
    QMenu::item { padding: 5px 22px; }
    QPushButton, QToolButton { background: #c8c7bd; border: 2px solid;
        border-top-color: white; border-left-color: white;
        border-right-color: #555750; border-bottom-color: #555750;
        padding: 5px 8px; min-height: 20px; border-radius: 0; }
    QPushButton:hover, QToolButton:hover { background: #d9d8cd; }
    QPushButton:pressed, QToolButton:pressed, QToolButton:checked {
        background: #bcbcaf; border-top-color: #555750; border-left-color: #555750;
        border-right-color: white; border-bottom-color: white; }
    QPushButton:focus, QToolButton:focus { outline: 1px dotted #10185d; }
    QPushButton:disabled, QToolButton:disabled { color: #85857e; background: #cecdc3; }
    QPushButton#retroNav { border: 1px solid transparent; text-align: left;
        padding: 7px 9px; background: transparent; }
    QPushButton#retroNav:checked { background: #0042bb; color: white;
        border: 1px solid #1c65d2; }
    QPushButton#retroNav:hover:!checked { background: #dcdcd2; border: 1px solid #f5f4ec; }
    QPushButton#restart { background: #e9d38c; }
    QPushButton#restart:disabled { background: #cecdc3; }
    QComboBox, QLineEdit { background: #faf5e1; border: 2px inset #eeeeea; border-radius: 0;
        padding: 3px 25px 3px 7px; min-height: 20px; }
    QComboBox:disabled { background: #e5e2d3; color: #626575; }
    QComboBox:focus { border: 2px inset #7c8eab; }
    QComboBox::drop-down { width: 22px; background: #c8c7bd; border: 1px outset #eeeeea; }
    QComboBox::down-arrow { image: url("@ASSET@/arrow-down.svg"); width: 9px; height: 6px; }
    QComboBox QAbstractItemView { background: #faf5e1; color: #10185d;
        selection-background-color: #003caa; selection-color: white; }
    QPlainTextEdit, QTableWidget, QTreeWidget { background: #f8f3de; color: #182963;
        border: 2px inset #eeeeea; border-radius: 0; padding: 2px;
        alternate-background-color: #eeead6; selection-background-color: #003caa;
        selection-color: white; gridline-color: #d3cfba; }
    QHeaderView::section { background: #d0cec1; color: #10185d;
        border: 1px outset #eeeeea; padding: 4px; }
    QScrollBar:vertical { background: #deded3; width: 17px; margin: 17px 0; }
    QScrollBar:horizontal { background: #deded3; height: 17px; margin: 0 17px; }
    QScrollBar::handle { background: #c0c0b7; border: 2px outset #eeeeea;
        min-width: 18px; min-height: 18px; }
    QScrollBar::add-line, QScrollBar::sub-line { background: #c8c7bd;
        border: 2px outset #eeeeea; width: 13px; height: 13px; subcontrol-origin: margin; }
    QScrollBar::sub-line:vertical { subcontrol-position: top; }
    QScrollBar::add-line:vertical { subcontrol-position: bottom; }
    QScrollBar::sub-line:horizontal { subcontrol-position: left; }
    QScrollBar::add-line:horizontal { subcontrol-position: right; }
    QScrollBar::add-page, QScrollBar::sub-page { background: #deded3; }
    QScrollBar::up-arrow:vertical { image: url("@ASSET@/arrow-up.svg"); width: 9px; height: 6px; }
    QScrollBar::down-arrow:vertical { image: url("@ASSET@/arrow-down.svg"); width: 9px; height: 6px; }
    QScrollBar::left-arrow:horizontal { image: url("@ASSET@/arrow-left.svg"); width: 6px; height: 9px; }
    QScrollBar::right-arrow:horizontal { image: url("@ASSET@/arrow-right.svg"); width: 6px; height: 9px; }
    QToolTip { color: #10185d; background: #ffffdf; border: 1px solid #333333; }
""".replace("@ASSET@", (Path(__file__).resolve().parent.parent / "assets" / "manager").as_posix())


class ManagerPresentation:
    """Move the same controls between their original slots and a retro dashboard."""

    NAMES = (
        "target_name_label", "connection_label", "instance_label", "status_label", "runtime_label",
        "start_button", "stop_button",
        "restart_button", "stamina_status_label", "stamina_remaining_label",
        "stamina_duration_combo", "end_stamina_wait_button", "detail_label", "error_label",
        "loot_status_label", "loot_grid", "loot_summary_label", "loot_detail_label",
        "loot_history", "loot_item_table", "log_caption", "log_view", "footer_label",
    )

    def __init__(self, window):
        self.window = window
        self.original = window.centralWidget()
        self.original_style = window.styleSheet()
        self.original_font = window.font()
        self.original_colors = dict(window.COLORS)
        self.original_icon = window.windowIcon()
        self.original_minimum = window.minimumSize()
        self.original_row_height = window.loot_item_table.verticalHeader().defaultSectionSize()
        self.origins = {}
        self.targets = {}
        self.saved = {}
        self.sizes = {"original": window.size()}
        self._remember_layout(self.original.widget().layout())
        for name in self.NAMES:
            widget = getattr(window, name)
            self.saved[name] = (widget.styleSheet(), widget.font(), widget.minimumSize(),
                                widget.maximumSize(), widget.sizePolicy())
        self.retro = self._build_retro()
        self.appearance = "original"
        self.destination = "dashboard"
        self._build_shell()

    def _remember_layout(self, layout):
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget:
                self.origins[widget] = (layout, index)
                if widget.layout():
                    self._remember_layout(widget.layout())
            elif item.layout():
                self._remember_layout(item.layout())

    @staticmethod
    def _panel(title, icon):
        frame = QFrame()
        frame.setObjectName("retroPanel")
        frame.setMinimumWidth(0)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        heading = QLabel("  " + title)
        heading.setObjectName("panelHeading")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        line = QWidget()
        row = QHBoxLayout(line)
        row.setContentsMargins(7, 0, 0, 0)
        row.setSpacing(0)
        image = QLabel()
        image.setPixmap(pixel_icon(icon).pixmap(20, 20))
        image.setFixedWidth(23)
        row.addWidget(image)
        row.addWidget(heading, 1)
        if title:
            outer.addWidget(line)
        else:
            line.deleteLater()
        content = QVBoxLayout()
        content.setContentsMargins(9, 5, 9, 5)
        content.setSpacing(5)
        outer.addLayout(content, 1)
        return frame, content

    def _place(self, name, layout, stretch=0):
        # Reserve the position now, including inside rows with spacers.
        slot = QWidget()
        slot.setObjectName("retroSlot")
        slot_layout = QVBoxLayout(slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        slot_layout.setSpacing(0)
        layout.addWidget(slot, stretch)
        self.targets[name] = (slot_layout, 0)

    def _build_shell(self):
        root = QWidget()
        body = QHBoxLayout(root)
        body.setContentsMargins(6, 6, 6, 4)
        body.setSpacing(10)
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(140)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(4, 8, 4, 6)
        side.setSpacing(4)
        self.nav_buttons = {}
        group = QButtonGroup(root)
        group.setExclusive(True)
        for key, text, icon in (("dashboard", "Dashboard", "dashboard"),
                                ("instance", "Instance", "account"),
                                ("settings", "Settings", "settings")):
            button = QPushButton(pixel_icon(icon, 24), text)
            button.setObjectName("retroNav")
            button.setIconSize(QSize(24, 24))
            button.setCheckable(key != "settings")
            if key != "settings":
                group.addButton(button)
            button.clicked.connect(lambda checked=False, destination=key: self.navigate(destination))
            self.nav_buttons[key] = button
            side.addWidget(button)
        self.nav_buttons["dashboard"].setChecked(True)
        side.addStretch(1)
        body.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        self.window.takeCentralWidget()
        self.stack.addWidget(self.original)
        self.stack.addWidget(self.retro)
        self.window.instance_page = self.window._build_instance_page()
        self.stack.addWidget(self.window.instance_page)
        body.addWidget(self.stack, 1)
        self.window.setCentralWidget(root)
        self.window.setMinimumWidth(self.original_minimum.width() + 156)

    def _build_retro(self):
        root = QWidget()
        root.setObjectName("retroBody")
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        right = QVBoxLayout()
        right.setSpacing(7)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(100)
        contents = QWidget()
        contents.setObjectName("retroContents")
        self.scroll.setWidget(contents)
        self.grid = QGridLayout(contents)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.grid.setColumnStretch(0, 3)
        self.grid.setColumnStretch(1, 2)
        account, area = self._panel("Instance", "account")
        row = QHBoxLayout()
        self._place("target_name_label", row)
        row.addStretch()
        self._place("connection_label", row)
        area.addLayout(row)
        self._place("instance_label", area)
        self.grid.addWidget(account, 0, 0)
        runtime, area = self._panel("Elapsed", "clock")
        self._place("runtime_label", area)
        self.grid.addWidget(runtime, 0, 1)
        farming, area = self._panel("Farming", "farming")
        self._place("status_label", area)
        self.completed_missions_label = QLabel("Completed this session: —")
        self.completed_missions_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.completed_missions_label.setToolTip("Completed missions in this farming session, including unrecognized loot.")
        area.addWidget(self.completed_missions_label)
        self.total_completed_missions_label = QLabel("Total completed missions: —")
        self.total_completed_missions_label.setStyleSheet("font-size: 11px; color: #425075;")
        area.addWidget(self.total_completed_missions_label)
        self._place("detail_label", area)
        self._place("error_label", area)
        self.grid.addWidget(farming, 1, 0)
        stamina, area = self._panel("Stamina", "hourglass")
        self._place("stamina_duration_combo", area)
        row = QHBoxLayout()
        self._place("stamina_status_label", row)
        row.addStretch()
        self._place("stamina_remaining_label", row)
        area.addLayout(row)
        self._place("end_stamina_wait_button", area)
        self.grid.addWidget(stamina, 1, 1)
        loot, area = self._panel("Loot", "loot")
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(QLabel("Sort by:"))
        self.loot_sort_combo = QComboBox()
        self.loot_sort_combo.setAccessibleName("Sort drops")
        for label, value in (("Type", "type"), ("Rarity", "rarity"), ("Recent", "recent")):
            self.loot_sort_combo.addItem(label, value)
        self.loot_sort_combo.setToolTip("Type groups drops by element. Rarity and Recent show highest or newest first.")
        selected = self.loot_sort_combo.findData(self.window._settings.value("retro_loot_sort", "type"))
        self.loot_sort_combo.setCurrentIndex(max(0, selected))
        self.loot_sort_combo.currentIndexChanged.connect(self._loot_sort_changed)
        row.addWidget(self.loot_sort_combo)
        area.addLayout(row)
        self._place("loot_status_label", area)
        self._place("loot_grid", area, 1)
        self.grid.addWidget(loot, 2, 0, 1, 2)
        history, area = self._panel("Recognition details", "loot")
        self.history_panel = history
        self._place("loot_summary_label", area)
        self._place("loot_detail_label", area)
        self._place("loot_item_table", area)
        self._place("loot_history", area)
        self.grid.addWidget(history, 3, 0, 1, 2)
        history.hide()
        self.grid.setRowStretch(2, 1)
        right.addWidget(self.scroll, 1)
        controls, area = self._panel("", "start")
        row = QHBoxLayout()
        row.setSpacing(10)
        for name in ("start_button", "stop_button", "restart_button"):
            self._place(name, row, 1)
        area.addLayout(row)
        right.addWidget(controls)
        log, area = self._panel("", "log")
        self.log_panel = log
        self._place("log_caption", area)
        self._place("log_view", area, 1)
        log.setMaximumHeight(136)
        right.addWidget(log)
        footer = QHBoxLayout()
        self._place("footer_label", footer, 1)
        self.monitor_label = QLabel("Checking process…")
        self.monitor_label.setTextFormat(Qt.TextFormat.PlainText)
        self.monitor_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer.addWidget(self.monitor_label)
        right.addLayout(footer)
        self.scroll.verticalScrollBar().valueChanged.connect(self.update_monitor_visibility)
        self.scroll.verticalScrollBar().rangeChanged.connect(self.update_monitor_visibility)
        body.addLayout(right, 1)
        return root

    def _loot_sort_changed(self):
        self.window._settings.setValue("retro_loot_sort", self.loot_sort_combo.currentData())
        self.window._settings.sync()
        self.window._update_loot_tiles()

    def update_completed_missions(self):
        window = self.window
        available = window._snapshot_known and not window._poll_error
        session = window._snapshot.get('session_completed_missions')
        total = window._snapshot.get("farm", {}).get("counts", {}).get("completed")
        session = session if available and type(session) is int and session >= 0 else '—'
        total = total if available and type(total) is int and total >= 0 else '—'
        self.completed_missions_label.setText(f"Completed this session: {session}")
        self.total_completed_missions_label.setText(f"Total completed missions: {total}")

    def update_detail_visibility(self, show_activity_log, show_recognition_details):
        """Keep optional diagnostics consistent across both presentations."""
        self.log_panel.setVisible(show_activity_log)
        self.history_panel.setVisible(show_recognition_details)
        self.window.log_caption.setVisible(show_activity_log)
        self.window.log_view.setVisible(show_activity_log)
        for name in ("loot_status_label", "loot_summary_label", "loot_detail_label",
                     "loot_item_table", "loot_history"):
            getattr(self.window, name).setVisible(show_recognition_details)
        self.window.loot_grid.show()

    def update_monitor_visibility(self, *args):
        status = self.window.status_label
        if (self.window._closed or not self.scroll.isAncestorOf(status)
                or self.window.appearance != "retro98"):
            return
        bounds = QRect(status.mapTo(self.scroll.viewport(), QPoint()), status.size())
        self.monitor_label.setVisible(not self.scroll.viewport().rect().contains(bounds))

    def navigate(self, destination):
        if destination == "settings":
            self.window.show_appearance_settings()
        elif destination in {"dashboard", "instance"}:
            self.destination = destination
            dashboard = self.retro if self.appearance == "retro98" else self.original
            self.stack.setCurrentWidget(self.window.instance_page if destination == "instance" else dashboard)
            self.nav_buttons[destination].setChecked(True)
            if destination == "instance" and not self.window._instances_known:
                self.window.refresh_instances()

    def apply(self, appearance):
        if appearance == self.appearance:
            return
        window = self.window
        self.sizes[self.appearance] = window.size()
        hidden = {name: getattr(window, name).isHidden() for name in self.NAMES}
        log_scroll = window.log_view.verticalScrollBar().value()
        if appearance == "retro98":
            window.setFont(QFont("Tahoma", 10))
            window.setStyleSheet(RETRO_STYLE)
            window.COLORS = RETRO_COLORS
            window.setWindowIcon(pixel_icon("globe", 32))
            for name in self.NAMES:
                widget = getattr(window, name)
                layout, stretch = self.targets[name]
                layout.addWidget(widget, stretch)
                widget.setStyleSheet("")
                widget.setMinimumSize(0, 0)
                widget.setMaximumSize(16777215, 16777215)
                policy = widget.sizePolicy()
                policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                policy.setVerticalPolicy(QSizePolicy.Policy.Maximum)
                if isinstance(widget, QLabel) and widget.wordWrap():
                    policy.setVerticalPolicy(QSizePolicy.Policy.Preferred)
                    policy.setHeightForWidth(True)
                if name in {"connection_label", "stamina_status_label", "stamina_remaining_label"}:
                    policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
                widget.setSizePolicy(policy)
                widget.setVisible(not hidden[name])
            window.runtime_label.setStyleSheet("font: 27px 'Lucida Console'; color: #171a15; background: #f6edcc; padding: 3px;")
            window.runtime_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            for name in ("start_button", "stop_button", "restart_button", "end_stamina_wait_button"):
                widget = getattr(window, name)
                widget.setIcon(pixel_icon(name.replace("_button", "").replace("end_stamina_wait", "hourglass")))
                widget.setMinimumHeight(29)
            window.restart_button.setObjectName("restart")
            window.runtime_label.setFixedHeight(39)
            window.status_label.setMaximumHeight(64)
            window.loot_grid.setMinimumSize(0, 260)
            window.loot_grid.setMaximumSize(16777215, 16777215)
            window.loot_grid.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            window.loot_item_table.setFixedHeight(86)
            window.loot_item_table.verticalHeader().setDefaultSectionSize(24)
            window.log_view.setMinimumHeight(90)
            window.log_view.setMaximumHeight(95)
            window.log_view.setFont(QFont("Lucida Console", 9))
            window.log_view.setStyleSheet("font-family: 'Consolas'; font-size: 12px;")
            window.error_label.setStyleSheet("color: #a02222; font-weight: bold;")
            window.setMinimumSize(760, 580)
        else:
            grouped = defaultdict(list)
            for name in self.NAMES:
                widget = getattr(window, name)
                layout, index = self.origins[widget]
                grouped[layout].append((index, name, widget))
            for layout, entries in grouped.items():
                for index, name, widget in sorted(entries):
                    # QVBoxLayout/QHBoxLayout retain every untouched label and spacer.
                    layout.insertWidget(index, widget, 1 if name == "log_view" else 0)
                    style, font, minimum, maximum, policy = self.saved[name]
                    widget.setStyleSheet(style)
                    widget.setFont(font)
                    widget.setMinimumSize(minimum)
                    widget.setMaximumSize(maximum)
                    widget.setSizePolicy(policy)
                    widget.setVisible(not hidden[name])
            window.runtime_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            window.loot_item_table.verticalHeader().setDefaultSectionSize(self.original_row_height)
            for name in ("start_button", "stop_button", "restart_button", "end_stamina_wait_button"):
                getattr(window, name).setIcon(QIcon())
            window.restart_button.setObjectName("")
            window.setFont(self.original_font)
            window.setStyleSheet(self.original_style)
            window.COLORS = self.original_colors
            window.setWindowIcon(self.original_icon)
            window.setMinimumSize(self.original_minimum.width() + 156, self.original_minimum.height())
        self.appearance = appearance
        self.navigate(self.destination)
        window.centralWidget().show()
        available = window.screen().availableGeometry().size()
        requested = self.sizes.get(appearance, QSize(980, 720))
        window.resize(min(requested.width(), max(window.minimumWidth(), available.width() - 32)),
                      min(requested.height(), max(window.minimumHeight(), available.height() - 48)))
        window.log_view.verticalScrollBar().setValue(log_scroll)
        QTimer.singleShot(0, self.update_monitor_visibility)
