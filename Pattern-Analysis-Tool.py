import sys
import os
import json
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QFileDialog, QListWidget,
                             QInputDialog, QMessageBox, QScrollArea, QTabWidget, QGroupBox,
                             QFormLayout, QSpinBox, QDoubleSpinBox, QCheckBox, QProgressBar,
                             QTextEdit, QTimeEdit, QTableWidget, QTableWidgetItem, QHeaderView,
                             QSystemTrayIcon, QMenu, QAction)
from PyQt5.QtCore import (Qt, QTimer, QRect, pyqtSignal, QSettings, QMetaObject, Q_ARG,
                           pyqtSlot, QTime, QDateTime)
from PyQt5.QtGui import QPixmap, QPainter, QColor, QIcon, QFont, QPalette
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
import pyautogui
import threading
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

APP_NAME = "PatternHawk"


class ScreenCapturePatternDetector(QMainWindow):

    update_progress = pyqtSignal(int)
    update_status = pyqtSignal(str)
    start_cooldown_signal = pyqtSignal(int)
    update_cooldown_label_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Idle")
        self.setGeometry(100, 100, 1000, 800)

        self.icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'PatternHawk.png')
        if os.path.exists(self.icon_path):
            self.setWindowIcon(QIcon(self.icon_path))

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        # Data model: 3 areas
        self.areas = [
            {"region": None, "templates": [], "active": True, "neglect_count": {}},
            {"region": None, "templates": [], "active": True, "neglect_count": {}},
            {"region": None, "templates": [], "active": True, "neglect_count": {}},
        ]
        self.global_hotkey = ""
        self.max_templates = 10

        # Scheduler data model: 96 blocks (15-min each)
        self.schedule_enabled = False
        self.schedule = [
            {"areas_active": [False, False, False], "capture_interval": 30, "cooldown": 5}
            for _ in range(96)
        ]
        self.current_block_index = -1

        self.settings = QSettings('PatternHawk', 'PatternHawk')

        self.main_folder = os.path.join(os.path.expanduser('~'), 'PatternHawk')
        self.data_file = os.path.join(self.main_folder, 'pattern_hawk_data.json')
        self.output_folder = os.path.join(self.main_folder, 'output')
        self.screenshot_folder = os.path.join(self.main_folder, 'screenshots')

        # Migration: check old folder for data
        old_folder = os.path.join(os.path.expanduser('~'), 'ScreenCapturePatternDetector')
        old_data_file = os.path.join(old_folder, 'pattern_detector_data.json')
        if not os.path.exists(self.data_file) and os.path.exists(old_data_file):
            os.makedirs(self.main_folder, exist_ok=True)
            import shutil
            shutil.copy2(old_data_file, self.data_file)

        for folder in [self.main_folder, self.output_folder, self.screenshot_folder]:
            os.makedirs(folder, exist_ok=True)

        self.loadData()
        self.setup_ui()
        self._setup_system_tray()

        self.current_selecting_area = 0
        self.last_displayed_image = None
        self.capture_in_progress = False
        self.capture_interval = 0
        self.really_quit = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.capture_and_detect)

        self.update_progress.connect(self.set_progress_bar_value)
        self.update_status.connect(self.set_status_label_text)

        self.cooldown_timer = QTimer(self)
        self.cooldown_timer.setSingleShot(True)
        self.cooldown_timer.timeout.connect(self.cooldown_finished)

        self.cooldown_update_timer = QTimer(self)
        self.cooldown_update_timer.timeout.connect(self.update_cooldown_label)

        self.cooldown_end_time = None
        self.in_cooldown = False

        self.start_cooldown_signal.connect(self.start_cooldown_timer_main_thread)
        self.update_cooldown_label_signal.connect(self.update_cooldown_label)

        self.progress_timer = QTimer(self)
        self.progress_timer.timeout.connect(self.update_progress_bar)
        self.elapsed_time = 0

        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self.update_countdown)
        self.remaining_delay = 0

        # Scheduler engine timer
        self.schedule_timer = QTimer(self)
        self.schedule_timer.timeout.connect(self.check_schedule)

        # If schedule was enabled on last close, start it
        if self.schedule_enabled:
            self.schedule_timer.start(5000)
            self.current_block_index = -1
            self.check_schedule()

    # ──────────────────────────────────────────────
    #  System Tray
    # ──────────────────────────────────────────────

    def _setup_system_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        if os.path.exists(self.icon_path):
            self.tray_icon.setIcon(QIcon(self.icon_path))
        else:
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().SP_ComputerIcon))
        self.tray_icon.setToolTip(APP_NAME)

        tray_menu = QMenu()

        show_action = QAction("Show / Hide", self)
        show_action.triggered.connect(self._toggle_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        start_action = QAction("Start Capture", self)
        start_action.triggered.connect(self.start_capture)
        tray_menu.addAction(start_action)

        stop_action = QAction("Stop Capture", self)
        stop_action.triggered.connect(self.stop_capture)
        tray_menu.addAction(stop_action)

        tray_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._toggle_window()

    def _quit_app(self):
        self.really_quit = True
        self.close()

    def _update_window_title(self, status=None):
        if status:
            self.setWindowTitle(f"{APP_NAME} — {status}")
            self.tray_icon.setToolTip(f"{APP_NAME} — {status}")
        else:
            self.setWindowTitle(f"{APP_NAME} — Idle")
            self.tray_icon.setToolTip(f"{APP_NAME} — Idle")

    # ──────────────────────────────────────────────
    #  Cooldown system
    # ──────────────────────────────────────────────

    @pyqtSlot(int)
    def start_cooldown_timer_main_thread(self, cooldown_seconds):
        self.cooldown_end_time = QDateTime.currentDateTime().addSecs(cooldown_seconds)
        self.cooldown_timer.start(cooldown_seconds * 1000)
        self.cooldown_update_timer.start(1000)
        self.update_cooldown_label()
        self.in_cooldown = True

    @pyqtSlot()
    def start_cooldown_timer(self):
        cooldown_seconds = self.cooldown_timer_input.value()
        self.start_cooldown_signal.emit(cooldown_seconds)

    @pyqtSlot()
    def update_cooldown_label(self):
        if self.cooldown_timer.isActive() and self.cooldown_end_time:
            remaining = QDateTime.currentDateTime().secsTo(self.cooldown_end_time)
            if remaining > 0:
                self.cooldown_timer_label.setText(f"<font color='red'>{remaining} seconds remaining</font>")
            else:
                self.cooldown_timer_label.setText("<font color='red'>Cooldown ending...</font>")
        else:
            self.cooldown_timer_label.setText("No active cooldown")
            self.cooldown_update_timer.stop()

    def cooldown_finished(self):
        self.in_cooldown = False
        self.cooldown_timer_label.setText("No active cooldown")
        self.log_message("Cooldown period finished. Ready for next hotkey.")

    # ──────────────────────────────────────────────
    #  UI Setup
    # ──────────────────────────────────────────────

    def setup_ui(self):
        self.tab_widget = QTabWidget()
        self.layout.addWidget(self.tab_widget)

        self._setup_capture_tab()
        self._setup_templates_tab()
        self._setup_scheduler_tab()
        self._setup_settings_tab()
        self._setup_logs_tab()

    def _setup_capture_tab(self):
        capture_tab = QWidget()
        capture_layout = QVBoxLayout(capture_tab)
        self.tab_widget.addTab(capture_tab, "Capture")

        # ── Area Selection ──
        area_group = QGroupBox("Area Selection")
        area_layout = QVBoxLayout(area_group)
        capture_layout.addWidget(area_group)

        self.select_area_buttons = []
        self.area_labels = []
        self.area_active_checkboxes = []
        self.area_status_labels = []

        for i in range(3):
            row = QHBoxLayout()

            # Status indicator
            status_lbl = QLabel("\u2B24")  # ● circle
            status_lbl.setStyleSheet("color: #666666; font-size: 14px;")
            status_lbl.setFixedWidth(20)
            status_lbl.setToolTip("Inactive")
            row.addWidget(status_lbl)

            btn = QPushButton(f"Select Area {i + 1}")
            btn.clicked.connect(lambda checked, idx=i: self.select_area(idx))
            row.addWidget(btn)

            label = QLabel("Not selected")
            if self.areas[i]["region"] is not None:
                r = self.areas[i]["region"]
                label.setText(f"{r.width()}x{r.height()}")
            row.addWidget(label)

            checkbox = QCheckBox("Active")
            checkbox.setChecked(self.areas[i]["active"])
            checkbox.stateChanged.connect(lambda state, idx=i: self.on_area_active_changed(idx, state))
            row.addWidget(checkbox)

            area_layout.addLayout(row)
            self.select_area_buttons.append(btn)
            self.area_labels.append(label)
            self.area_active_checkboxes.append(checkbox)
            self.area_status_labels.append(status_lbl)

        # ── Capture Settings ──
        capture_group = QGroupBox("Capture Settings")
        capture_settings_layout = QFormLayout(capture_group)
        capture_layout.addWidget(capture_group)

        self.delay_timer = QTimeEdit()
        self.delay_timer.setDisplayFormat("hh:mm:ss")
        self.delay_timer.setTime(QTime(0, 0, 0))
        capture_settings_layout.addRow("Delay Start (hh:mm:ss):", self.delay_timer)

        time_input_layout = QHBoxLayout()
        self.capture_minutes_input = QSpinBox()
        self.capture_minutes_input.setRange(0, 60)
        self.capture_minutes_input.setValue(0)
        time_input_layout.addWidget(QLabel("Minutes:"))
        time_input_layout.addWidget(self.capture_minutes_input)

        self.capture_seconds_input = QSpinBox()
        self.capture_seconds_input.setRange(0, 59)
        self.capture_seconds_input.setValue(0)
        time_input_layout.addWidget(QLabel("Seconds:"))
        time_input_layout.addWidget(self.capture_seconds_input)
        capture_settings_layout.addRow("Capture Interval:", time_input_layout)

        # ── Start / Stop / Pause buttons ──
        button_layout = QHBoxLayout()

        self.start_capture_button = QPushButton("Start Capture")
        self.start_capture_button.setStyleSheet(
            "QPushButton { background-color: #2d7d46; } QPushButton:hover { background-color: #35a055; }")
        self.start_capture_button.clicked.connect(self.start_capture)
        button_layout.addWidget(self.start_capture_button)

        self.stop_capture_button = QPushButton("Stop Capture")
        self.stop_capture_button.setStyleSheet(
            "QPushButton { background-color: #a03535; } QPushButton:hover { background-color: #c04545; }")
        self.stop_capture_button.clicked.connect(self.stop_capture)
        self.stop_capture_button.setEnabled(False)
        button_layout.addWidget(self.stop_capture_button)

        self.pause_capture_button = QPushButton("Pause Capture")
        self.pause_capture_button.clicked.connect(self.pause_capture)
        self.pause_capture_button.setEnabled(False)
        button_layout.addWidget(self.pause_capture_button)

        capture_settings_layout.addRow(button_layout)

        self.pause_duration_input = QSpinBox()
        self.pause_duration_input.setRange(1, 3600)
        self.pause_duration_input.setValue(120)
        capture_settings_layout.addRow("Pause Duration (seconds):", self.pause_duration_input)

        # ── Status & Progress ──
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("font-weight: bold; padding: 4px;")
        capture_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        capture_layout.addWidget(self.progress_bar)

        # ── Screenshot Display ──
        self.screenshot_label = QLabel()
        self.screenshot_label.setAlignment(Qt.AlignCenter)
        self.screenshot_scroll = QScrollArea()
        self.screenshot_scroll.setWidget(self.screenshot_label)
        self.screenshot_scroll.setWidgetResizable(True)
        capture_layout.addWidget(self.screenshot_scroll)

    def _setup_templates_tab(self):
        templates_tab = QWidget()
        templates_tab_layout = QVBoxLayout(templates_tab)
        self.tab_widget.addTab(templates_tab, "Templates")

        templates_scroll = QScrollArea()
        templates_scroll.setWidgetResizable(True)
        templates_scroll_content = QWidget()
        templates_layout = QVBoxLayout(templates_scroll_content)
        templates_scroll.setWidget(templates_scroll_content)
        templates_tab_layout.addWidget(templates_scroll)

        self.template_lists = []

        for i in range(3):
            group = QGroupBox(f"Area {i + 1} Templates")
            group_layout = QVBoxLayout(group)

            controls = QHBoxLayout()
            add_btn = QPushButton("Add Template")
            add_btn.clicked.connect(lambda checked, idx=i: self.addTemplate(idx))
            controls.addWidget(add_btn)

            del_btn = QPushButton("Delete Template")
            del_btn.clicked.connect(lambda checked, idx=i: self.deleteTemplate(idx))
            controls.addWidget(del_btn)

            group_layout.addLayout(controls)

            template_list = QListWidget()
            template_list.setMaximumHeight(130)
            group_layout.addWidget(template_list)

            templates_layout.addWidget(group)
            self.template_lists.append(template_list)

        for i in range(3):
            self.updateTemplateList(i)

    def _setup_scheduler_tab(self):
        scheduler_tab = QWidget()
        scheduler_layout = QVBoxLayout(scheduler_tab)
        self.tab_widget.addTab(scheduler_tab, "Scheduler")

        # ── Enable toggle + status ──
        toggle_layout = QHBoxLayout()

        self.schedule_enable_checkbox = QCheckBox("Enable Schedule")
        self.schedule_enable_checkbox.setChecked(self.schedule_enabled)
        self.schedule_enable_checkbox.stateChanged.connect(self.on_schedule_enabled_changed)
        toggle_layout.addWidget(self.schedule_enable_checkbox)

        self.schedule_status_label = QLabel("")
        self._update_schedule_status_label()
        toggle_layout.addWidget(self.schedule_status_label)
        toggle_layout.addStretch()

        scheduler_layout.addLayout(toggle_layout)

        # ── Schedule table ──
        self.schedule_table = QTableWidget(96, 6)
        self.schedule_table.setHorizontalHeaderLabels([
            "Time", "Area 1", "Area 2", "Area 3", "Interval (s)", "Cooldown (s)"
        ])

        for row in range(96):
            hour = (row * 15) // 60
            minute = (row * 15) % 60
            end_hour = hour + (minute + 15) // 60
            end_minute = (minute + 15) % 60
            time_str = f"{hour:02d}:{minute:02d} - {end_hour:02d}:{end_minute:02d}"

            time_item = QTableWidgetItem(time_str)
            time_item.setFlags(time_item.flags() & ~Qt.ItemIsEditable)
            self.schedule_table.setItem(row, 0, time_item)

            block = self.schedule[row]

            for area_idx in range(3):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                item.setCheckState(Qt.Checked if block["areas_active"][area_idx] else Qt.Unchecked)
                self.schedule_table.setItem(row, 1 + area_idx, item)

            interval_item = QTableWidgetItem(str(block["capture_interval"]))
            interval_item.setTextAlignment(Qt.AlignCenter)
            self.schedule_table.setItem(row, 4, interval_item)

            cooldown_item = QTableWidgetItem(str(block["cooldown"]))
            cooldown_item.setTextAlignment(Qt.AlignCenter)
            self.schedule_table.setItem(row, 5, cooldown_item)

        self.schedule_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for col in range(1, 6):
            self.schedule_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Stretch)
        self.schedule_table.verticalHeader().setVisible(False)

        scheduler_layout.addWidget(self.schedule_table)

        # Auto-scroll to current block
        current_block = self.get_current_block_index()
        self.schedule_table.scrollToItem(
            self.schedule_table.item(current_block, 0),
            QTableWidget.PositionAtCenter)
        self._highlight_current_block(current_block, -1)

        # ── Action buttons ──
        actions_layout = QHBoxLayout()

        save_btn = QPushButton("Save Schedule")
        save_btn.setStyleSheet(
            "QPushButton { background-color: #2d7d46; } QPushButton:hover { background-color: #35a055; }")
        save_btn.clicked.connect(self.save_schedule_from_table)
        actions_layout.addWidget(save_btn)

        for i in range(3):
            sel_btn = QPushButton(f"All A{i + 1}")
            sel_btn.clicked.connect(lambda checked, idx=i: self.schedule_toggle_area(idx, True))
            actions_layout.addWidget(sel_btn)

            clr_btn = QPushButton(f"Clear A{i + 1}")
            clr_btn.clicked.connect(lambda checked, idx=i: self.schedule_toggle_area(idx, False))
            actions_layout.addWidget(clr_btn)

        scheduler_layout.addLayout(actions_layout)

    def _setup_settings_tab(self):
        settings_tab = QWidget()
        settings_layout = QFormLayout(settings_tab)
        self.tab_widget.addTab(settings_tab, "Settings")

        self.global_hotkey_input = QLineEdit()
        self.global_hotkey_input.setText(self.global_hotkey)
        self.global_hotkey_input.setPlaceholderText("e.g., ctrl+shift+a")
        settings_layout.addRow("Global Hotkey:", self.global_hotkey_input)
        settings_layout.addRow(QLabel("Hotkey executed when ALL active areas match simultaneously"))

        self.neglect_matched = QCheckBox()
        self.neglect_matched.setChecked(self.settings.value('neglect_matched', True, type=bool))
        settings_layout.addRow("Neglect Previously Matched Templates:", self.neglect_matched)
        settings_layout.addRow(QLabel("Ignore templates that have been matched in the previous cycle"))

        self.cooldown_timer_input = QSpinBox()
        self.cooldown_timer_input.setRange(1, 900)
        self.cooldown_timer_input.setValue(self.settings.value('cooldown_timer', 5, type=int))
        settings_layout.addRow("Cooldown Timer (seconds):", self.cooldown_timer_input)
        settings_layout.addRow(QLabel("Time to wait before executing another hotkey"))

        self.cooldown_timer_label = QLabel("No active cooldown")
        settings_layout.addRow("Cooldown Remaining:", self.cooldown_timer_label)

        self.reset_button = QPushButton("Reset Process")
        self.reset_button.clicked.connect(self.reset_process)
        settings_layout.addRow(self.reset_button)

        save_settings_button = QPushButton("Save Settings")
        save_settings_button.clicked.connect(self.save_settings)
        settings_layout.addRow(save_settings_button)

    def _setup_logs_tab(self):
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.tab_widget.addTab(log_tab, "Logs")

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)

        clear_logs_btn = QPushButton("Clear Logs")
        clear_logs_btn.clicked.connect(self.log_text.clear)
        log_layout.addWidget(clear_logs_btn)

    # ──────────────────────────────────────────────
    #  Area active toggle + status indicators
    # ──────────────────────────────────────────────

    def on_area_active_changed(self, area_index, state):
        self.areas[area_index]["active"] = (state == Qt.Checked)
        self._update_area_status(area_index)

    def _update_area_status(self, area_index, matched=None):
        lbl = self.area_status_labels[area_index]
        area = self.areas[area_index]

        if not area["active"]:
            lbl.setStyleSheet("color: #666666; font-size: 14px;")
            lbl.setToolTip("Inactive")
        elif matched is True:
            lbl.setStyleSheet("color: #00cc00; font-size: 14px;")
            lbl.setToolTip("Matched")
        elif matched is False:
            lbl.setStyleSheet("color: #cc0000; font-size: 14px;")
            lbl.setToolTip("No match")
        else:
            lbl.setStyleSheet("color: #2a82da; font-size: 14px;")
            lbl.setToolTip("Active — waiting")

    def _reset_area_statuses(self):
        for i in range(3):
            self._update_area_status(i)

    # ──────────────────────────────────────────────
    #  Data persistence
    # ──────────────────────────────────────────────

    def loadData(self):
        if not os.path.exists(self.data_file):
            return
        try:
            with open(self.data_file, 'r') as f:
                data = json.load(f)

            if isinstance(data, dict) and "areas" in data:
                self.global_hotkey = data.get("global_hotkey", "")
                for i, area_data in enumerate(data.get("areas", [])):
                    if i < 3:
                        self.areas[i]["templates"] = area_data.get("templates", [])
                        region = area_data.get("region")
                        if region:
                            self.areas[i]["region"] = QRect(
                                region["x"], region["y"], region["w"], region["h"]
                            )
                        self.areas[i]["active"] = area_data.get("active", True)

                self.schedule_enabled = data.get("schedule_enabled", False)
                saved_schedule = data.get("schedule", [])
                for i, block in enumerate(saved_schedule):
                    if i < 96:
                        self.schedule[i] = {
                            "areas_active": block.get("areas_active", [False, False, False]),
                            "capture_interval": block.get("capture_interval", 30),
                            "cooldown": block.get("cooldown", 5),
                        }

            elif isinstance(data, list):
                self.areas[0]["templates"] = [
                    {"path": t["path"], "similarity": t["similarity"]} for t in data
                ]
                if data and "hotkey" in data[0]:
                    self.global_hotkey = data[0]["hotkey"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def saveData(self):
        data = {
            "global_hotkey": self.global_hotkey,
            "areas": [],
            "schedule_enabled": self.schedule_enabled,
            "schedule": self.schedule,
        }
        for area in self.areas:
            area_data = {
                "templates": area["templates"],
                "region": None,
                "active": area["active"]
            }
            if area["region"] is not None:
                area_data["region"] = {
                    "x": area["region"].x(),
                    "y": area["region"].y(),
                    "w": area["region"].width(),
                    "h": area["region"].height()
                }
            data["areas"].append(area_data)

        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)

    # ──────────────────────────────────────────────
    #  Template management (per area)
    # ──────────────────────────────────────────────

    def addTemplate(self, area_index):
        area = self.areas[area_index]
        if len(area["templates"]) >= self.max_templates:
            QMessageBox.warning(self, "Template Limit",
                                f"Area {area_index + 1} can have up to {self.max_templates} templates.")
            return

        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getOpenFileName(
            self, f"Select Template for Area {area_index + 1}", "",
            "Images (*.png *.xpm *.jpg *.bmp);;All Files (*)", options=options)
        if filePath:
            similarity, ok = QInputDialog.getDouble(
                self, "Similarity Threshold",
                "Enter similarity threshold (0-1):", 0.8, 0, 1, 2)
            if ok:
                area["templates"].append({"path": filePath, "similarity": similarity})
                self.updateTemplateList(area_index)
                self.saveData()
                self.status_label.setText(
                    f"Template added to Area {area_index + 1}: {os.path.basename(filePath)}")

    def deleteTemplate(self, area_index):
        template_list = self.template_lists[area_index]
        currentRow = template_list.currentRow()
        if currentRow != -1:
            del self.areas[area_index]["templates"][currentRow]
            self.updateTemplateList(area_index)
            self.saveData()
            self.status_label.setText(f"Template deleted from Area {area_index + 1}")

    def updateTemplateList(self, area_index):
        template_list = self.template_lists[area_index]
        template_list.clear()
        for i, template in enumerate(self.areas[area_index]["templates"]):
            file_name = os.path.basename(template['path'])
            template_list.addItem(
                f"{i + 1}. {file_name} (Confidence: {template['similarity']})")

    # ──────────────────────────────────────────────
    #  Area selection
    # ──────────────────────────────────────────────

    def select_area(self, area_index):
        self.current_selecting_area = area_index
        self.hide()
        self.area_selector = AreaSelector()
        self.area_selector.areaSelected.connect(self.on_area_selected)
        self.area_selector.showFullScreen()

    def on_area_selected(self, rect):
        idx = self.current_selecting_area
        self.areas[idx]["region"] = rect
        self.area_labels[idx].setText(f"{rect.width()}x{rect.height()}")
        self.status_label.setText(f"Area {idx + 1} selected: {rect.width()}x{rect.height()}")
        self.saveData()
        self.show()

    # ──────────────────────────────────────────────
    #  Scheduler
    # ──────────────────────────────────────────────

    def get_current_block_index(self):
        now = QTime.currentTime()
        return (now.hour() * 60 + now.minute()) // 15

    def on_schedule_enabled_changed(self, state):
        self.schedule_enabled = (state == Qt.Checked)

        if self.schedule_enabled:
            self.global_hotkey = self.global_hotkey_input.text().strip()
            if not self.global_hotkey:
                QMessageBox.warning(self, "Error",
                                    "Set a Global Hotkey in Settings before enabling the schedule.")
                self.schedule_enable_checkbox.setChecked(False)
                self.schedule_enabled = False
                return

            self.save_schedule_from_table()

            self.start_capture_button.setEnabled(False)
            self.stop_capture_button.setEnabled(False)
            self.pause_capture_button.setEnabled(False)
            self.delay_timer.setEnabled(False)
            self.capture_minutes_input.setEnabled(False)
            self.capture_seconds_input.setEnabled(False)
            for cb in self.area_active_checkboxes:
                cb.setEnabled(False)

            self.current_block_index = -1
            self.schedule_timer.start(5000)
            self.check_schedule()
            self._update_window_title("Scheduled")
            self.log_message("Scheduler ENABLED. Manual controls disabled.")
        else:
            self.schedule_timer.stop()

            if self.capture_in_progress:
                self.capture_in_progress = False
                self.timer.stop()
                self.progress_timer.stop()

            self.start_capture_button.setEnabled(True)
            self.stop_capture_button.setEnabled(False)
            self.delay_timer.setEnabled(True)
            self.capture_minutes_input.setEnabled(True)
            self.capture_seconds_input.setEnabled(True)
            for cb in self.area_active_checkboxes:
                cb.setEnabled(True)

            self.progress_bar.setValue(0)
            self.status_label.setText("Ready (manual mode)")
            self._update_window_title("Idle")
            self._reset_area_statuses()
            self.log_message("Scheduler DISABLED. Manual controls re-enabled.")

        self._update_schedule_status_label()
        self.saveData()

    def check_schedule(self):
        if not self.schedule_enabled:
            return

        block_idx = self.get_current_block_index()
        if block_idx == self.current_block_index:
            return

        prev_block = self.current_block_index
        self.current_block_index = block_idx
        block = self.schedule[block_idx]

        hour = (block_idx * 15) // 60
        minute = (block_idx * 15) % 60
        self.log_message(f"Schedule block transition: {hour:02d}:{minute:02d} (block {block_idx})")

        for i in range(3):
            self.areas[i]["active"] = block["areas_active"][i]
            self.area_active_checkboxes[i].setChecked(block["areas_active"][i])
            self._update_area_status(i)

        any_active = any(
            block["areas_active"][i]
            and self.areas[i]["region"] is not None
            and len(self.areas[i]["templates"]) > 0
            for i in range(3)
        )

        if any_active:
            self.capture_interval = block["capture_interval"] * 1000
            self.cooldown_timer_input.setValue(block["cooldown"])

            active_names = [f"A{i+1}" for i in range(3) if block["areas_active"][i]]
            interval_sec = block["capture_interval"]
            self.log_message(
                f"Active areas: {active_names}. Interval: {interval_sec}s. Cooldown: {block['cooldown']}s")

            self.capture_in_progress = True
            self.timer.start(self.capture_interval)
            self.elapsed_time = 0
            self.progress_timer.start(1000)
            self.status_label.setText(
                f"Scheduled: {', '.join(active_names)} active. Interval: {interval_sec}s")
            self._update_window_title(f"Scheduled — {', '.join(active_names)}")
        else:
            if self.capture_in_progress:
                self.capture_in_progress = False
                self.timer.stop()
                self.progress_timer.stop()
                self.progress_bar.setValue(0)
            self.log_message("No active areas in this block. Capture paused.")
            self.status_label.setText("Scheduled: no active areas in current block")
            self._update_window_title("Scheduled — Idle")

        self._update_schedule_status_label()
        self._highlight_current_block(block_idx, prev_block)

        # Auto-scroll to current block
        self.schedule_table.scrollToItem(
            self.schedule_table.item(block_idx, 0),
            QTableWidget.PositionAtCenter)

    def _update_schedule_status_label(self):
        if not self.schedule_enabled:
            self.schedule_status_label.setText("Schedule: OFF (manual mode)")
            return

        block_idx = self.get_current_block_index()
        hour = (block_idx * 15) // 60
        minute = (block_idx * 15) % 60
        block = self.schedule[block_idx]
        active = [f"A{i+1}" for i in range(3) if block["areas_active"][i]]
        active_str = ", ".join(active) if active else "none"
        self.schedule_status_label.setText(
            f"Schedule: ON | Block: {hour:02d}:{minute:02d} | Active: {active_str}")

    def _highlight_current_block(self, current, previous):
        if 0 <= previous < 96:
            for col in range(6):
                item = self.schedule_table.item(previous, col)
                if item:
                    item.setBackground(QColor(25, 25, 25))

        if 0 <= current < 96:
            for col in range(6):
                item = self.schedule_table.item(current, col)
                if item:
                    item.setBackground(QColor(42, 130, 218, 80))

    def save_schedule_from_table(self):
        for row in range(96):
            areas_active = []
            for area_idx in range(3):
                item = self.schedule_table.item(row, 1 + area_idx)
                areas_active.append(item.checkState() == Qt.Checked if item else False)

            interval_item = self.schedule_table.item(row, 4)
            cooldown_item = self.schedule_table.item(row, 5)

            try:
                interval = int(interval_item.text()) if interval_item else 30
                interval = max(1, interval)
            except ValueError:
                interval = 30

            try:
                cooldown = int(cooldown_item.text()) if cooldown_item else 5
                cooldown = max(1, cooldown)
            except ValueError:
                cooldown = 5

            self.schedule[row] = {
                "areas_active": areas_active,
                "capture_interval": interval,
                "cooldown": cooldown,
            }

        self.saveData()
        self.log_message("Schedule saved.")

        if self.schedule_enabled:
            self.current_block_index = -1
            self.check_schedule()

    def schedule_toggle_area(self, area_index, checked):
        for row in range(96):
            item = self.schedule_table.item(row, 1 + area_index)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    # ──────────────────────────────────────────────
    #  Capture control
    # ──────────────────────────────────────────────

    def start_capture(self):
        if self.schedule_enabled:
            QMessageBox.information(self, "Scheduler Active",
                                    "Disable the scheduler to use manual capture.")
            return

        active_areas = [
            a for a in self.areas
            if a["active"] and a["region"] is not None and len(a["templates"]) > 0
        ]
        if not active_areas:
            QMessageBox.warning(self, "Error",
                                "At least one area must be active with a selected region and templates.")
            return

        self.global_hotkey = self.global_hotkey_input.text().strip()
        if not self.global_hotkey:
            QMessageBox.warning(self, "Error",
                                "Please set a Global Hotkey in the Settings tab.")
            return

        minutes = self.capture_minutes_input.value()
        seconds = self.capture_seconds_input.value()
        total_seconds = minutes * 60 + seconds

        if total_seconds == 0:
            QMessageBox.warning(self, "Error",
                                "Please set a capture interval greater than 0 seconds.")
            return

        self.capture_interval = total_seconds * 1000

        delay_time = self.delay_timer.time()
        delay_seconds = delay_time.hour() * 3600 + delay_time.minute() * 60 + delay_time.second()

        if minutes > 0 and seconds > 0:
            interval_text = f"Capture interval: {minutes} min and {seconds} sec"
        elif minutes > 0:
            interval_text = f"Capture interval: {minutes} min"
        else:
            interval_text = f"Capture interval: {seconds} sec"

        active_count = len(active_areas)
        self.log_message(f"Starting capture with {active_count} active area(s). {interval_text}")

        if delay_seconds > 0:
            self.remaining_delay = delay_seconds
            self.countdown_timer.start(1000)
            self.update_countdown()
            status_text = f"Starting after {delay_time.toString('hh:mm:ss')}. {interval_text}"
        else:
            status_text = f"Capture starting now. {interval_text}"
            self.start_capture_after_delay()

        self.status_label.setText(status_text)
        self.start_capture_button.setEnabled(False)
        self.stop_capture_button.setEnabled(True)
        self.pause_capture_button.setEnabled(True)
        self._set_area_buttons_enabled(False)
        self.progress_bar.setValue(0)
        self._reset_area_statuses()
        self._update_window_title(f"Capturing ({active_count} areas)")

        if delay_seconds == 0:
            self.elapsed_time = 0
            self.progress_timer.start(1000)

    def stop_capture(self):
        self.capture_in_progress = False
        self.timer.stop()
        self.progress_timer.stop()
        if hasattr(self, 'pause_timer'):
            self.pause_timer.stop()

        self.start_capture_button.setEnabled(True)
        self.stop_capture_button.setEnabled(False)
        self.pause_capture_button.setEnabled(False)
        self._set_area_buttons_enabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Capture stopped")
        self._update_window_title("Idle")
        self._reset_area_statuses()
        self.log_message("Capture stopped by user.")

    def _set_area_buttons_enabled(self, enabled):
        for btn in self.select_area_buttons:
            btn.setEnabled(enabled)

    def update_countdown(self):
        if self.remaining_delay > 0:
            hours, remainder = divmod(self.remaining_delay, 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            self.status_label.setText(f"Capture will start in: {time_str}")
            self.remaining_delay -= 1
        else:
            self.countdown_timer.stop()
            self.start_capture_after_delay()

    def start_capture_after_delay(self):
        self.capture_in_progress = True
        self.timer.start(self.capture_interval)

        self.elapsed_time = 0
        self.progress_timer.start(1000)

        minutes = self.capture_interval // 60000
        seconds = (self.capture_interval % 60000) // 1000
        if minutes > 0 and seconds > 0:
            status_text = f"Capture started. Next in {minutes} min and {seconds} sec"
        elif minutes > 0:
            status_text = f"Capture started. Next in {minutes} min"
        else:
            status_text = f"Capture started. Next in {seconds} sec"
        self.status_label.setText(status_text)

    def pause_capture(self):
        if self.capture_in_progress:
            self.capture_in_progress = False
            self.timer.stop()
            self.progress_timer.stop()
            self.pause_duration = self.pause_duration_input.value()
            self.pause_timer = QTimer(self)
            self.pause_timer.timeout.connect(self.resume_capture)
            self.pause_timer.start(self.pause_duration * 1000)
            self.status_label.setText(f"Capture paused for {self.pause_duration} seconds")
            self.pause_capture_button.setEnabled(False)
            self.start_capture_button.setEnabled(False)
            self._set_area_buttons_enabled(False)
            self._update_window_title("Paused")

    def resume_capture(self):
        self.capture_in_progress = True
        self.timer.start(self.capture_interval)
        self.progress_timer.start(1000)
        self.status_label.setText("Capture resumed")
        self.pause_capture_button.setEnabled(True)
        self.start_capture_button.setEnabled(False)
        self._set_area_buttons_enabled(False)
        active_count = sum(1 for a in self.areas if a["active"])
        self._update_window_title(f"Capturing ({active_count} areas)")

    def update_progress_bar(self):
        if self.capture_in_progress and self.capture_interval > 0:
            total_seconds = self.capture_interval // 1000
            self.elapsed_time += 1
            if self.elapsed_time > total_seconds:
                self.elapsed_time = 0

            progress = (self.elapsed_time / total_seconds) * 100
            self.progress_bar.setValue(int(progress))

            remaining_time = total_seconds - self.elapsed_time
            minutes, seconds = divmod(remaining_time, 60)
            if not self.schedule_enabled:
                if minutes > 0 and seconds > 0:
                    status_text = f"Next capture in {minutes} min and {seconds} sec"
                elif minutes > 0:
                    status_text = f"Next capture in {minutes} min"
                else:
                    status_text = f"Next capture in {seconds} sec"
                self.status_label.setText(status_text)

    # ──────────────────────────────────────────────
    #  Capture & Detection (multi-area + AND logic)
    # ──────────────────────────────────────────────

    def capture_and_detect(self):
        if self.capture_in_progress:
            self.log_message("Starting capture and detection cycle")
            threading.Thread(target=self._capture_and_detect_thread).start()
        self.elapsed_time = 0
        self.progress_bar.setValue(0)

    def _capture_and_detect_thread(self):
        try:
            area_results = {}
            area_matched_data = {}

            for i, area in enumerate(self.areas):
                if not area["active"] or area["region"] is None or not area["templates"]:
                    continue

                self.log_message(f"Capturing Area {i + 1}")
                screenshot = self.capture_screen(area["region"], i)
                self.log_message(f"Area {i + 1} screenshot: {screenshot}")

                self.log_message(f"Detecting patterns in Area {i + 1}")
                matched, patterns = self.detect_pattern_for_area(
                    screenshot, area["templates"], area["neglect_count"])

                area_results[i] = matched
                if patterns:
                    area_matched_data[i] = (patterns, screenshot)

                status = "MATCHED" if matched else "NO MATCH"
                self.log_message(f"Area {i + 1}: {status} ({len(patterns)} pattern(s))")

                # Update status indicator on main thread
                QMetaObject.invokeMethod(
                    self, "_update_area_status_slot",
                    Qt.QueuedConnection,
                    Q_ARG(int, i), Q_ARG(bool, matched))

            if not area_results:
                self.log_message("No active areas with regions and templates configured.")
                self.update_status.emit("No active areas configured.")
                return

            all_matched = all(area_results.values())

            if all_matched and not self.in_cooldown:
                self.log_message("ALL active areas matched! Executing global hotkey.")
                self.perform_hotkey(self.global_hotkey)
                status_msg = f"All {len(area_results)} area(s) matched. Hotkey executed: {self.global_hotkey}"
            elif all_matched and self.in_cooldown:
                self.log_message("All areas matched but in cooldown. Hotkey not executed.")
                status_msg = f"All areas matched. Hotkey skipped (cooldown active)."
            else:
                matched_areas = [i + 1 for i, m in area_results.items() if m]
                unmatched_areas = [i + 1 for i, m in area_results.items() if not m]
                self.log_message(
                    f"AND condition NOT met. Matched: {matched_areas}, Unmatched: {unmatched_areas}")
                status_msg = f"Matched: Area {matched_areas}, No match: Area {unmatched_areas}. Hotkey not executed."

            self.update_status.emit(status_msg)

            if area_matched_data:
                self.process_combined_visualization(area_matched_data, area_results)

        except Exception as e:
            self.log_message(f"Error during capture and detect: {str(e)}")

    @pyqtSlot(int, bool)
    def _update_area_status_slot(self, area_index, matched):
        self._update_area_status(area_index, matched)

    def capture_screen(self, region, area_index):
        screen = QApplication.primaryScreen()
        screenshot = screen.grabWindow(
            0, region.x(), region.y(), region.width(), region.height())
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"screenshot_area{area_index + 1}_{timestamp}.png"
        filepath = os.path.join(self.screenshot_folder, filename)
        screenshot.save(filepath)
        return filepath

    def detect_pattern_for_area(self, screenshot_path, templates, neglect_count):
        neglect_matched = self.neglect_matched.isChecked()

        try:
            stock_chart = cv2.imread(screenshot_path)
            if stock_chart is None:
                self.log_message(f"Warning: Could not read screenshot {screenshot_path}")
                return (False, [])
            stock_chart_rgb = cv2.cvtColor(stock_chart, cv2.COLOR_BGR2RGB)

            all_matched_patterns = []

            for template in templates:
                template_path = template['path']

                if (neglect_matched and template_path in neglect_count
                        and neglect_count[template_path] > 0):
                    self.log_message(f"Skipping template {os.path.basename(template_path)} (neglect)")
                    neglect_count[template_path] -= 1
                    if neglect_count[template_path] == 0:
                        del neglect_count[template_path]
                    continue

                pattern_img = cv2.imread(template_path)
                if pattern_img is None:
                    self.log_message(f"Warning: Could not load template {template_path}")
                    continue
                pattern_rgb = cv2.cvtColor(pattern_img, cv2.COLOR_BGR2RGB)

                best_match_score = -1
                best_match_loc = None
                best_match_scale = None
                best_match_shape = None

                scales = sorted(set(np.linspace(0.5, 2.0, 20).tolist() + [1.0]))
                for scale in scales:
                    resized = cv2.resize(pattern_rgb, None, fx=scale, fy=scale)

                    if (resized.shape[0] > stock_chart_rgb.shape[0]
                            or resized.shape[1] > stock_chart_rgb.shape[1]):
                        continue

                    result = cv2.matchTemplate(
                        stock_chart_rgb, resized, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, max_loc = cv2.minMaxLoc(result)

                    if max_val > best_match_score:
                        best_match_score = max_val
                        best_match_loc = max_loc
                        best_match_scale = scale
                        best_match_shape = resized.shape

                if (best_match_score >= template['similarity']
                        and best_match_loc is not None):
                    x, y = best_match_loc
                    h, w = best_match_shape[:2]
                    roi = stock_chart_rgb[y:y + h, x:x + w]

                    resized_best = cv2.resize(pattern_rgb, None,
                                              fx=best_match_scale, fy=best_match_scale)

                    ssim_value = self.calculate_ssim(roi, resized_best)

                    roi_hist = cv2.calcHist(
                        [roi], [0, 1, 2], None,
                        [8, 8, 8], [0, 256, 0, 256, 0, 256])
                    tmpl_hist = cv2.calcHist(
                        [resized_best], [0, 1, 2], None,
                        [8, 8, 8], [0, 256, 0, 256, 0, 256])
                    hist_sim = cv2.compareHist(
                        roi_hist, tmpl_hist, cv2.HISTCMP_CORREL)

                    combined = (0.4 * best_match_score
                                + 0.4 * ssim_value
                                + 0.2 * hist_sim)

                    all_matched_patterns.append(
                        (best_match_loc, best_match_shape, combined,
                         template_path, best_match_scale))

                    neglect_count[template_path] = 1

            return (len(all_matched_patterns) > 0, all_matched_patterns)

        except Exception as e:
            self.log_message(f"Error in pattern detection: {str(e)}")
            return (False, [])

    def calculate_ssim(self, img1, img2):
        min_dim = min(img1.shape[0], img1.shape[1], img2.shape[0], img2.shape[1])
        win_size = min_dim if min_dim % 2 == 1 else min_dim - 1
        win_size = max(3, win_size)
        try:
            return ssim(img1, img2, win_size=win_size, channel_axis=-1, data_range=255)
        except ValueError:
            return 0

    def process_combined_visualization(self, area_matched_data, area_results):
        try:
            num_areas = max(len(area_results), 1)

            fig, axes = plt.subplots(1, num_areas, figsize=(6 * num_areas, 5))
            if num_areas == 1:
                axes = [axes]

            area_indices = sorted(area_results.keys())
            for ax_idx, area_idx in enumerate(area_indices):
                ax = axes[ax_idx]
                matched = area_results[area_idx]

                if area_idx in area_matched_data:
                    patterns, screenshot_path = area_matched_data[area_idx]
                    img = cv2.imread(screenshot_path)
                    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    ax.imshow(img_rgb)

                    patterns.sort(key=lambda x: x[2], reverse=True)
                    for i, pattern in enumerate(patterns):
                        x, y = pattern[0]
                        h, w = pattern[1][:2]
                        score = pattern[2]
                        tmpl_path = pattern[3]
                        scale = pattern[4]

                        rect = plt.Rectangle(
                            (x, y), w, h, edgecolor='lime', facecolor='none', linewidth=2)
                        ax.add_patch(rect)
                        ax.text(x, y - 5,
                                f'{os.path.basename(tmpl_path)}\n'
                                f'Score: {score:.3f} | Scale: {scale:.2f}',
                                color='lime', fontsize=7,
                                bbox=dict(boxstyle='round,pad=0.2',
                                          facecolor='black', alpha=0.7))

                status = "MATCHED" if matched else "NO MATCH"
                color = 'green' if matched else 'red'
                ax.set_title(f'Area {area_idx + 1}: {status}', color=color, fontweight='bold')
                ax.axis('off')

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            output_path = os.path.join(self.output_folder, f"detection_{timestamp}.png")
            plt.tight_layout()
            plt.savefig(output_path, dpi=100, bbox_inches='tight')
            plt.close(fig)

            self.display_image(output_path)

        except Exception as e:
            self.log_message(f"Error in visualization: {str(e)}")

    # ──────────────────────────────────────────────
    #  Hotkey execution
    # ──────────────────────────────────────────────

    def perform_hotkey(self, hotkey):
        try:
            pyautogui.hotkey(*hotkey.split('+'))
            self.log_message(f"Hotkey performed: {hotkey}")
            self.start_cooldown_timer()
        except Exception as e:
            self.log_message(f"Error performing hotkey {hotkey}: {str(e)}")

    # ──────────────────────────────────────────────
    #  Settings, reset, display, logging
    # ──────────────────────────────────────────────

    def save_settings(self):
        self.global_hotkey = self.global_hotkey_input.text().strip()
        self.settings.setValue('neglect_matched', self.neglect_matched.isChecked())
        self.settings.setValue('cooldown_timer', self.cooldown_timer_input.value())
        self.saveData()
        QMessageBox.information(self, "Settings Saved", "Your settings have been saved.")

    def reset_process(self):
        reply = QMessageBox.question(
            self, 'Reset Confirmation',
            "Are you sure you want to reset the entire process?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:
            self.timer.stop()
            self.progress_timer.stop()
            self.cooldown_timer.stop()
            self.cooldown_update_timer.stop()

            if self.schedule_enabled:
                self.schedule_enabled = False
                self.schedule_enable_checkbox.setChecked(False)
                self.schedule_timer.stop()

            self.capture_in_progress = False
            self.in_cooldown = False
            self.elapsed_time = 0
            self.cooldown_end_time = None
            self.current_block_index = -1

            self.progress_bar.setValue(0)
            self.status_label.setText("Ready")
            self.cooldown_timer_label.setText("No active cooldown")
            self.start_capture_button.setEnabled(True)
            self.stop_capture_button.setEnabled(False)
            self.pause_capture_button.setEnabled(False)
            self._set_area_buttons_enabled(True)
            self.delay_timer.setEnabled(True)
            self.capture_minutes_input.setEnabled(True)
            self.capture_seconds_input.setEnabled(True)
            for cb in self.area_active_checkboxes:
                cb.setEnabled(True)

            for area in self.areas:
                area["neglect_count"].clear()

            self.capture_minutes_input.setValue(0)
            self.capture_seconds_input.setValue(0)
            self.screenshot_label.clear()
            self.last_displayed_image = None

            self._update_window_title("Idle")
            self._reset_area_statuses()
            self.log_message("Process has been reset")
            QMessageBox.information(self, "Reset Complete", "The process has been reset successfully.")

    def log_message(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        QMetaObject.invokeMethod(self.log_text, "append",
                                 Qt.QueuedConnection,
                                 Q_ARG(str, log_entry))

    def set_progress_bar_value(self, value):
        self.progress_bar.setValue(value)

    def set_status_label_text(self, text):
        self.status_label.setText(text)

    def display_image(self, image_path):
        self.last_displayed_image = image_path
        pixmap = QPixmap(image_path)
        scaled_pixmap = pixmap.scaled(
            self.screenshot_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.screenshot_label.setPixmap(scaled_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.last_displayed_image:
            self.display_image(self.last_displayed_image)

    def closeEvent(self, event):
        if hasattr(self, 'cooldown_update_timer'):
            self.cooldown_update_timer.stop()
        if hasattr(self, 'schedule_timer'):
            self.schedule_timer.stop()
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        event.accept()


class AreaSelector(QWidget):
    areaSelected = pyqtSignal(QRect)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background:transparent;")
        screen = QApplication.primaryScreen()
        self.setGeometry(screen.geometry())

        self.begin = None
        self.end = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(255, 0, 0))
        painter.setBrush(QColor(255, 0, 0, 30))
        painter.drawRect(0, 0, self.width(), self.height())

        if self.begin and self.end:
            rect = QRect(self.begin, self.end).normalized()
            painter.setPen(QColor(0, 255, 0))
            painter.setBrush(QColor(0, 255, 0, 30))
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        self.begin = event.pos()
        self.end = self.begin
        self.update()

    def mouseMoveEvent(self, event):
        self.end = event.pos()
        self.update()

    def mouseReleaseEvent(self, event):
        self.end = event.pos()
        rect = QRect(self.begin, self.end).normalized()
        if rect.width() > 0 and rect.height() > 0:
            self.areaSelected.emit(rect)
        self.close()


if __name__ == '__main__':
    app = QApplication(sys.argv)

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'PatternHawk.png')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    app.setStyle('Fusion')

    font = QFont("Arial", 10)
    app.setFont(font)

    dark_palette = QPalette()
    dark_palette.setColor(QPalette.Window, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.WindowText, Qt.white)
    dark_palette.setColor(QPalette.Base, QColor(25, 25, 25))
    dark_palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ToolTipBase, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ToolTipText, Qt.white)
    dark_palette.setColor(QPalette.Text, Qt.white)
    dark_palette.setColor(QPalette.Button, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ButtonText, Qt.white)
    dark_palette.setColor(QPalette.BrightText, Qt.red)
    dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(dark_palette)
    app.setStyleSheet("""
        QToolTip {
            color: #ffffff;
            background-color: #2a82da;
            border: 1px solid white;
        }
        QGroupBox {
            border: 1px solid #555555;
            margin-top: 0.5em;
            padding-top: 0.5em;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 3px 0 3px;
        }
        QProgressBar {
            border: 1px solid #555555;
            text-align: center;
            height: 20px;
        }
        QProgressBar::chunk {
            background-color: #2a82da;
        }
    """)

    window = ScreenCapturePatternDetector()
    window.show()
    sys.exit(app.exec_())
