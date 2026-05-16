import sys
import os
import json
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QFileDialog, QListWidget, 
                             QInputDialog, QMessageBox, QScrollArea, QTabWidget, QGroupBox,
                             QFormLayout, QSpinBox, QDoubleSpinBox, QCheckBox, QProgressBar)
from PyQt5.QtCore import Qt, QTimer, QRect, pyqtSignal, QSettings
from PyQt5.QtGui import QPixmap, QPainter, QColor, QIcon, QFont, QPalette  # Add QPalette here
import cv2
import numpy as np
#import matplotlib.pyplot as plt
from skimage.metrics import structural_similarity as ssim
import pyautogui
import threading
import time
from PyQt5.QtWidgets import QTextEdit
from PyQt5.QtCore import QMetaObject, Q_ARG, QTimer, pyqtSignal, pyqtSlot, QTime, Qt, QRect, QSettings, QDateTime
#from PyQt5.QtCore import Q_ARG
#from PyQt5.QtCore import QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QTimeEdit
#from PyQt5.QtCore import QTime

# Fix for Matplotlib UserWarning
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
#from PyQt5.QtCore import pyqtSlot
#from PyQt5.QtCore import Q_ARG
#from PyQt5.QtCore import Qt, QTimer, QRect, pyqtSignal, QSettings, QDateTime

class ScreenCapturePatternDetector(QMainWindow):

    update_progress = pyqtSignal(int)
    update_status = pyqtSignal(str)
    start_cooldown_signal = pyqtSignal(int)
    update_cooldown_label_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Advanced Screen Capture Pattern Detector')
        self.setGeometry(100, 100, 1000, 800)
        # Update icon setting
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bull-logo.jpg')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            print(f"Warning: Icon file not found at {icon_path}")

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        self.templates = []
        self.matched_templates = {}
        self.neglect_count = {}
        self.settings = QSettings('YourCompany', 'ScreenCapturePatternDetector')
        self.max_templates = 10

        self.main_folder = os.path.join(os.path.expanduser('~'), 'ScreenCapturePatternDetector')
        self.data_file = os.path.join(self.main_folder, 'pattern_detector_data.json')
        self.output_folder = os.path.join(self.main_folder, 'output')
        self.screenshot_folder = os.path.join(self.main_folder, 'screenshots')

        for folder in [self.main_folder, self.output_folder, self.screenshot_folder]:
            os.makedirs(folder, exist_ok=True)

        self.loadData()
        self.setup_ui()

        self.selected_region = None
        self.capture_in_progress = False
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

        # Connect signals
        self.start_cooldown_signal.connect(self.start_cooldown_timer_main_thread)
        self.update_cooldown_label_signal.connect(self.update_cooldown_label)

        self.progress_timer = QTimer(self)
        self.progress_timer.timeout.connect(self.update_progress_bar)
        self.elapsed_time = 0

        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self.update_countdown)
        self.remaining_delay = 0

    @pyqtSlot(int)
    def start_cooldown_timer_main_thread(self, cooldown_seconds):
        self.cooldown_end_time = QDateTime.currentDateTime().addSecs(cooldown_seconds)
        self.cooldown_timer.start(cooldown_seconds * 1000)
        self.cooldown_update_timer.start(1000)  # Update every second
        self.update_cooldown_label()
        self.in_cooldown = True

    @pyqtSlot()
    def start_cooldown_timer(self):
        cooldown_seconds = self.cooldown_timer_input.value()
        self.start_cooldown_signal.emit(cooldown_seconds)

    def setup_ui(self):
        # Create tab widget
        self.tab_widget = QTabWidget()
        self.layout.addWidget(self.tab_widget)

        # Capture tab
        capture_tab = QWidget()
        capture_layout = QVBoxLayout(capture_tab)
        self.tab_widget.addTab(capture_tab, "Capture")

        # Update the screenshot controls section
        screenshot_group = QGroupBox("Screenshot Controls")
        screenshot_layout = QFormLayout(screenshot_group)
        capture_layout.addWidget(screenshot_group)

        self.delay_timer = QTimeEdit()
        self.delay_timer.setDisplayFormat("hh:mm:ss")
        self.delay_timer.setTime(QTime(0, 0, 0))
        screenshot_layout.addRow("Delay Start (hh:mm:ss):", self.delay_timer)

        self.select_area_button = QPushButton("Select Area")
        self.select_area_button.clicked.connect(self.select_area)
        screenshot_layout.addRow("Area:", self.select_area_button)

        # Create a horizontal layout for minutes and seconds inputs
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

        screenshot_layout.addRow("Capture interval:", time_input_layout)

        self.start_capture_button = QPushButton("Start Capture")
        self.start_capture_button.clicked.connect(self.start_capture)
        screenshot_layout.addRow(self.start_capture_button)

        self.pause_duration_input = QSpinBox()
        self.pause_duration_input.setRange(1, 3600)  # 1 second to 1 hour
        self.pause_duration_input.setValue(120)  # Default to 120 seconds
        screenshot_layout.addRow("Pause Duration (seconds):", self.pause_duration_input)

        # Add Pause button
        self.pause_capture_button = QPushButton("Pause Capture")
        self.pause_capture_button.clicked.connect(self.pause_capture)
        self.pause_capture_button.setEnabled(False)
        screenshot_layout.addRow(self.pause_capture_button)

        # Status and progress
        self.status_label = QLabel("Ready")
        capture_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        capture_layout.addWidget(self.progress_bar)

        # Screenshot display
        self.screenshot_label = QLabel()
        self.screenshot_label.setAlignment(Qt.AlignCenter)
        self.screenshot_scroll = QScrollArea()
        self.screenshot_scroll.setWidget(self.screenshot_label)
        self.screenshot_scroll.setWidgetResizable(True)
        capture_layout.addWidget(self.screenshot_scroll)

        # Templates tab
        templates_tab = QWidget()
        templates_layout = QVBoxLayout(templates_tab)
        self.tab_widget.addTab(templates_tab, "Templates")

        # Template controls
        template_group = QGroupBox("Template Controls")
        template_control_layout = QHBoxLayout(template_group)
        templates_layout.addWidget(template_group)

        self.add_template_button = QPushButton("Add Template")
        self.add_template_button.clicked.connect(self.addTemplate)
        template_control_layout.addWidget(self.add_template_button)

        self.delete_template_button = QPushButton("Delete Template")
        self.delete_template_button.clicked.connect(self.deleteTemplate)
        template_control_layout.addWidget(self.delete_template_button)

        # Template list
        self.template_list = QListWidget()
        templates_layout.addWidget(self.template_list)

        self.updateTemplateList()

        # Settings tab
        settings_tab = QWidget()
        settings_layout = QFormLayout(settings_tab)
        self.tab_widget.addTab(settings_tab, "Settings")

        self.neglect_matched = QCheckBox()
        self.neglect_matched.setChecked(self.settings.value('neglect_matched', True, type=bool))
        settings_layout.addRow("Neglect Previously Matched Templates:", self.neglect_matched)
        settings_layout.addRow(QLabel("Ignore templates that have been matched in the previous cycle"))

        self.cooldown_timer_input = QSpinBox()
        self.cooldown_timer_input.setRange(1, 900)  # 1 second to 15 minutes
        self.cooldown_timer_input.setValue(self.settings.value('cooldown_timer', 5, type=int))
        settings_layout.addRow("Hotkey Initiated Cooldown Timer (seconds):", self.cooldown_timer_input)
        settings_layout.addRow(QLabel("Time to wait before executing another hotkey (1 for no cooldown)"))

        self.cooldown_timer_label = QLabel("No active cooldown")
        settings_layout.addRow("Cooldown Remaining:", self.cooldown_timer_label)

        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.tab_widget.addTab(log_tab, "Logs")

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text) 

        # Add Reset button
        self.reset_button = QPushButton("Reset Process")
        self.reset_button.clicked.connect(self.reset_process)
        settings_layout.addRow(self.reset_button)

        save_settings_button = QPushButton("Save Settings")
        save_settings_button.clicked.connect(self.save_settings)
        settings_layout.addRow(save_settings_button)

    def reset_process(self):
        reply = QMessageBox.question(self, 'Reset Confirmation', 
                                     "Are you sure you want to reset the entire process?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            # Stop all timers
            self.timer.stop()
            self.progress_timer.stop()
            self.cooldown_timer.stop()
            self.cooldown_update_timer.stop()

            # Reset all relevant variables
            self.capture_in_progress = False
            self.in_cooldown = False
            self.elapsed_time = 0
            self.cooldown_end_time = None

            # Reset UI elements
            self.progress_bar.setValue(0)
            self.status_label.setText("Ready")
            self.cooldown_timer_label.setText("No active cooldown")
            self.start_capture_button.setEnabled(True)
            self.pause_capture_button.setEnabled(False)
            self.select_area_button.setEnabled(True)

            # Clear any ongoing detections or matches
            self.matched_templates.clear()
            self.neglect_count.clear()

            # Reset capture inputs
            self.capture_minutes_input.setValue(0)
            self.capture_seconds_input.setValue(0)

            # Clear the screenshot display
            self.screenshot_label.clear()

            self.log_message("Process has been reset")
            QMessageBox.information(self, "Reset Complete", "The process has been reset successfully.")

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
            self.select_area_button.setEnabled(False)

    def resume_capture(self):
        self.capture_in_progress = True
        self.timer.start(self.capture_interval)
        self.progress_timer.start(1000)
        self.status_label.setText("Capture resumed")
        self.pause_capture_button.setEnabled(True)
        self.start_capture_button.setEnabled(False)
        self.select_area_button.setEnabled(False)

    def log_message(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        QMetaObject.invokeMethod(self.log_text, "append",
                                 Qt.QueuedConnection,
                                 Q_ARG(str, log_entry))
    def save_settings(self):
        self.settings.setValue('neglect_matched', self.neglect_matched.isChecked())
        self.settings.setValue('capture_minutes', self.capture_minutes_input.value())
        self.settings.setValue('capture_seconds', self.capture_seconds_input.value())
        self.settings.setValue('selected_region', self.selected_region)
        self.settings.setValue('cooldown_timer', self.cooldown_timer_input.value())
        QMessageBox.information(self, "Settings Saved", "Your settings have been saved.")

    def load_settings(self):
        self.neglect_matched.setChecked(self.settings.value('neglect_matched', True, type=bool))
        self.capture_minutes_input.setValue(self.settings.value('capture_minutes', 0, type=int))
        self.capture_seconds_input.setValue(self.settings.value('capture_seconds', 0, type=int))
        self.selected_region = self.settings.value('selected_region', None)
        self.cooldown_timer_input.setValue(self.settings.value('cooldown_timer', 30, type=int))

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

    # In any method that might be called from a non-main thread
    def some_method_that_starts_cooldown(self):
        QMetaObject.invokeMethod(self, "start_cooldown_timer", Qt.QueuedConnection)

    def set_progress_bar_value(self, value):
        self.progress_bar.setValue(value)

    def set_status_label_text(self, text):
        self.status_label.setText(text)

    def loadData(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f:
                self.templates = json.load(f)

    def saveData(self):
        with open(self.data_file, 'w') as f:
            json.dump(self.templates, f)

    def addTemplate(self):
        if len(self.templates) >= self.max_templates:
            QMessageBox.warning(self, "Template Limit Reached", f"You can only add up to {self.max_templates} templates.")
            return

        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getOpenFileName(self, "Select Template Image", "", "Images (*.png *.xpm *.jpg *.bmp);;All Files (*)", options=options)
        if filePath:
            similarity, ok = QInputDialog.getDouble(self, "Input Similarity Threshold", 
                                                    "Enter similarity threshold (0-1):", 0.8, 0, 1, 2)
            if ok:
                while True:
                    hotkey, ok = QInputDialog.getText(self, "Input Hotkey", "Enter hotkey (e.g., ctrl+shift+a):")
                    if ok and hotkey.strip():
                        self.templates.append({"path": filePath, "similarity": similarity, "hotkey": hotkey})
                        self.updateTemplateList()
                        self.saveData()
                        self.status_label.setText(f"Template added: {os.path.basename(filePath)}")
                        break
                    elif not ok:
                        return  # User cancelled
                    else:
                        QMessageBox.warning(self, "Invalid Input", "Hotkey cannot be empty. Please try again.")

    def updateTemplateList(self):
        self.template_list.clear()
        for i, template in enumerate(self.templates):
            file_name = os.path.basename(template['path'])
            self.template_list.addItem(f"{i+1}. {file_name} (Similarity: {template['similarity']}, Hotkey: {template['hotkey']})")

    def deleteTemplate(self):
        currentRow = self.template_list.currentRow()
        if currentRow != -1:
            del self.templates[currentRow]
            self.updateTemplateList()
            self.saveData()
            self.status_label.setText("Template deleted")

    def select_area(self):
        self.hide()
        self.area_selector = AreaSelector()
        self.area_selector.areaSelected.connect(self.on_area_selected)
        self.area_selector.showFullScreen()

    def on_area_selected(self, rect):
        self.selected_region = rect
        self.status_label.setText(f"Selected area: {rect.width()}x{rect.height()}")
        self.show()

    def start_capture(self):
        if not self.selected_region:
            QMessageBox.warning(self, "Error", "Please select an area first.")
            return

        minutes = self.capture_minutes_input.value()
        seconds = self.capture_seconds_input.value()
        total_seconds = minutes * 60 + seconds

        if total_seconds == 0:
            QMessageBox.warning(self, "Error", "Please set a capture interval greater than 0 seconds.")
            return

        self.capture_interval = total_seconds * 1000  # Convert to milliseconds

        delay_time = self.delay_timer.time()
        delay_seconds = delay_time.hour() * 3600 + delay_time.minute() * 60 + delay_time.second()

        if minutes > 0 and seconds > 0:
            interval_text = f"Capture interval: {minutes} minutes and {seconds} seconds"
        elif minutes > 0:
            interval_text = f"Capture interval: {minutes} minutes"
        else:
            interval_text = f"Capture interval: {seconds} seconds"

        if delay_seconds > 0:
            self.remaining_delay = delay_seconds
            self.countdown_timer.start(1000)  # Update every second
            self.update_countdown()
            status_text = f"Capture will start after {delay_time.toString('hh:mm:ss')}. {interval_text}"
        else:
            status_text = f"Capture starting now. {interval_text}"
            self.start_capture_after_delay()

        self.status_label.setText(status_text)
        self.start_capture_button.setEnabled(False)
        self.pause_capture_button.setEnabled(True)
        self.select_area_button.setEnabled(False)
        self.progress_bar.setValue(0)

        # If there's no delay, start the progress timer immediately
        if delay_seconds == 0:
            self.elapsed_time = 0
            self.progress_timer.start(1000)  # Update every second

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
        
        # Start the progress timer
        self.elapsed_time = 0
        self.progress_timer.start(1000)  # Update every second

        minutes = self.capture_interval // 60000
        seconds = (self.capture_interval % 60000) // 1000
        if minutes > 0 and seconds > 0:
            status_text = f"Capture started. Next capture in {minutes} minutes and {seconds} seconds"
        elif minutes > 0:
            status_text = f"Capture started. Next capture in {minutes} minutes"
        else:
            status_text = f"Capture started. Next capture in {seconds} seconds"
        self.status_label.setText(status_text)

    def stop_capture(self):
        self.capture_in_progress = False
        self.timer.stop()
        self.progress_timer.stop()
        if hasattr(self, 'pause_timer'):
            self.pause_timer.stop()
        self.start_capture_button.setEnabled(True)
        self.pause_capture_button.setEnabled(False)
        self.select_area_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Capture stopped")
        if hasattr(self, 'hotkey_timer'):
            self.hotkey_timer.stop()

    def update_progress_bar(self):
        if self.capture_in_progress:
            total_seconds = self.capture_minutes_input.value() * 60 + self.capture_seconds_input.value()
            self.elapsed_time += 1
            if self.elapsed_time > total_seconds:
                self.elapsed_time = 0

            progress = (self.elapsed_time / total_seconds) * 100
            self.progress_bar.setValue(int(progress))

            # Update status label
            remaining_time = total_seconds - self.elapsed_time
            minutes, seconds = divmod(remaining_time, 60)
            if minutes > 0 and seconds > 0:
                status_text = f"Next capture in {minutes} minutes and {seconds} seconds"
            elif minutes > 0:
                status_text = f"Next capture in {minutes} minutes"
            else:
                status_text = f"Next capture in {seconds} seconds"
            self.status_label.setText(status_text)

    def capture_and_detect(self):
        if self.capture_in_progress and self.selected_region:
            self.log_message("Starting capture and detection cycle")
            threading.Thread(target=self._capture_and_detect_thread).start()
        self.elapsed_time = 0
        self.progress_bar.setValue(0)

    def _capture_and_detect_thread(self):
        try:
            self.log_message("Capturing screenshot")
            screenshot = self.capture_screen(self.selected_region)
            self.log_message(f"Screenshot captured: {screenshot}")
            self.log_message("Starting pattern detection")
            self.detect_pattern(screenshot)
        except Exception as e:
            self.log_message(f"Error during capture and detect: {str(e)}")

    def capture_screen(self, region):
        screen = QApplication.primaryScreen()
        screenshot = screen.grabWindow(0, region.x(), region.y(), region.width(), region.height())
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"screenshot_{timestamp}.png"
        filepath = os.path.join(self.screenshot_folder, filename)
        screenshot.save(filepath)
        return filepath

    def calculate_ssim(self, img1, img2):
        min_dim = min(img1.shape[0], img1.shape[1], img2.shape[0], img2.shape[1])
        win_size = min_dim if min_dim % 2 == 1 else min_dim - 1
        win_size = max(3, win_size)
        
        try:
            return ssim(img1, img2, win_size=win_size, channel_axis=-1, data_range=255)
        except ValueError:
            print(f"Warning: SSIM calculation failed for images of shape {img1.shape} and {img2.shape}")
            return 0

    @pyqtSlot()
    def start_hotkey_timer(self):
        gap_seconds = self.hotkey_gap_input.value()
        self.hotkey_timer.start(gap_seconds * 1000)

    def queue_hotkey(self, hotkey):
        self.log_message(f"Hotkey detected: {hotkey}")
        if not self.hotkey_timer.isActive():
            self.log_message(f"Executing hotkey immediately: {hotkey}")
            self.perform_hotkey(hotkey)
            gap_seconds = self.hotkey_gap_input.value()
            self.log_message(f"Starting hotkey timer for {gap_seconds} seconds")
            QMetaObject.invokeMethod(self, "start_hotkey_timer", Qt.QueuedConnection)
        else:
            self.log_message(f"Queueing hotkey: {hotkey}")
            self.hotkey_queue.append(hotkey)

    def perform_hotkey(self, hotkey):
        try:
            pyautogui.hotkey(*hotkey.split('+'))
            self.log_message(f"Hotkey performed: {hotkey}")
            self.start_cooldown_timer()
        except Exception as e:
            self.log_message(f"Error performing hotkey {hotkey}: {str(e)}")

    @pyqtSlot(str)
    def _perform_hotkey(self, hotkey):
        try:
            pyautogui.hotkey(*hotkey.split('+'))
            self.log_message(f"Hotkey performed: {hotkey}")
        except Exception as e:
            self.log_message(f"Error performing hotkey {hotkey}: {str(e)}")

    @pyqtSlot()
    def process_queued_hotkeys(self):
        self.log_message("Timer expired. Processing queued hotkeys.")
        execution_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_message(f"Execution time: {execution_time}")
        
        if not self.hotkey_queue:
            self.log_message("No hotkeys in queue.")
        else:
            self.log_message(f"Number of hotkeys in queue: {len(self.hotkey_queue)}")
        
        while self.hotkey_queue:
            hotkey = self.hotkey_queue.pop(0)
            self.log_message(f"Executing queued hotkey: {hotkey}")
            self.perform_hotkey(hotkey)
        
        self.log_message("Hotkey queue processed and timer stopped")

    def detect_pattern(self, screenshot_path):
        neglect_matched = self.neglect_matched.isChecked()

        self.log_message(f"Processing screenshot: {screenshot_path}")
        self.update_status.emit(f"Processing screenshot: {screenshot_path}")

        try:
            stock_chart = cv2.imread(screenshot_path)
            stock_chart_rgb = cv2.cvtColor(stock_chart, cv2.COLOR_BGR2RGB)

            all_matched_patterns = []

            for i, template in enumerate(self.templates):
                template_path = template['path']
                
                if neglect_matched and template_path in self.neglect_count and self.neglect_count[template_path] > 0:
                    self.log_message(f"Skipping template {template_path} due to neglect count")
                    self.neglect_count[template_path] -= 1
                    if self.neglect_count[template_path] == 0:
                        del self.neglect_count[template_path]
                    continue

                self.log_message(f"Processing template: {template_path}")
                
                pattern_template = cv2.imread(template_path)
                pattern_template_rgb = cv2.cvtColor(pattern_template, cv2.COLOR_BGR2RGB)

                for scale in np.linspace(0.5, 2.0, 20):
                    resized_template = cv2.resize(pattern_template_rgb, None, fx=scale, fy=scale)
                    
                    if resized_template.shape[0] > stock_chart_rgb.shape[0] or resized_template.shape[1] > stock_chart_rgb.shape[1]:
                        continue

                    result = cv2.matchTemplate(stock_chart_rgb, resized_template, cv2.TM_CCOEFF_NORMED)
                    locations = np.where(result >= template['similarity'])
                    
                    for pt in zip(*locations[::-1]):
                        x, y = pt
                        h, w = resized_template.shape[:2]
                        roi = stock_chart_rgb[y:y+h, x:x+w]

                        ssim_value = self.calculate_ssim(roi, resized_template)

                        roi_hist = cv2.calcHist([roi], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
                        template_hist = cv2.calcHist([resized_template], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
                        hist_similarity = cv2.compareHist(roi_hist, template_hist, cv2.HISTCMP_CORREL)

                        combined_similarity = 0.4 * result[y, x] + 0.4 * ssim_value + 0.2 * hist_similarity

                        all_matched_patterns.append((pt, resized_template.shape, combined_similarity, template_path, scale, template['hotkey']))
                        
                        self.neglect_count[template_path] = 1

            if all_matched_patterns:
                self.log_message(f"Found {len(all_matched_patterns)} matching patterns")
                self.process_matched_patterns(all_matched_patterns, stock_chart_rgb, screenshot_path)
            else:
                self.log_message("No patterns matched the specified similarity thresholds.")
                self.update_status.emit("No patterns matched the specified similarity thresholds.")
                self.display_image(screenshot_path)

        except Exception as e:
            self.log_message(f"An error occurred during pattern detection: {str(e)}")

    def process_matched_patterns(self, all_matched_patterns, stock_chart_rgb, screenshot_path):
        all_matched_patterns.sort(key=lambda x: x[2], reverse=True)
        self.log_message(f"Processing {len(all_matched_patterns)} matched patterns")

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.imshow(stock_chart_rgb)

        executed_hotkey = False

        for i, pattern in enumerate(all_matched_patterns):
            x, y = pattern[0]
            h, w = pattern[1][:2]
            similarity_score = pattern[2]
            pattern_template_path = pattern[3]
            scale = pattern[4]
            hotkey = pattern[5]

            rect = plt.Rectangle((x, y), w, h, edgecolor='r', facecolor='none', linewidth=2)
            ax.add_patch(rect)
            plt.text(x, y, f'{i+1}. Similarity: {similarity_score:.4f}\nScale: {scale:.2f}\n{os.path.basename(pattern_template_path)}', color='red', fontsize=8)

            if not self.in_cooldown and not executed_hotkey:
                self.log_message(f"Executing hotkey: {hotkey}")
                self.perform_hotkey(hotkey)
                executed_hotkey = True
                self.in_cooldown = True
                cooldown_seconds = self.cooldown_timer_input.value()
                QMetaObject.invokeMethod(self, "start_cooldown_timer", Qt.QueuedConnection)
            else:
                self.log_message(f"Pattern detected but hotkey not executed (in cooldown): {hotkey}")

        plt.title(f'Detected Patterns: {len(all_matched_patterns)}')

        output_filename = f"detected_patterns_{os.path.basename(screenshot_path)}"
        output_path = os.path.join(self.output_folder, output_filename)
        plt.savefig(output_path)
        plt.close(fig)

        self.display_image(output_path)
        
        status_msg = f"Patterns detected: {len(all_matched_patterns)}. Hotkey executed: {'Yes' if executed_hotkey else 'No'}"
        self.update_status.emit(status_msg)
        self.log_message(status_msg)

    def display_image(self, image_path):
        pixmap = QPixmap(image_path)
        scaled_pixmap = pixmap.scaled(self.screenshot_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.screenshot_label.setPixmap(scaled_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.screenshot_label.pixmap():
            self.display_image(self.screenshot_label.pixmap().toImage())

    def closeEvent(self, event):
        if hasattr(self, 'cooldown_update_timer'):
            self.cooldown_update_timer.stop()
        super().closeEvent(event)

class AreaSelector(QWidget):
    areaSelected = pyqtSignal(QRect)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background:transparent;")
        self.setGeometry(QApplication.desktop().geometry())
        
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

    # Set application icon
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), r'E:\Screen-Capture\bull-logo.jpg')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    else:
        print(f"Warning: Icon file not found at {icon_path}")

    app.setStyle('Fusion')  # Use Fusion style for a modern look
    
    # Set application-wide font
    font = QFont("Arial", 10)
    app.setFont(font)
    
    # Set dark theme
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
            border: 1px solid #ffffff;
            margin-top: 0.5em;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 3px 0 3px;
        }
    """)
    
    window = ScreenCapturePatternDetector()
    window.show()
    sys.exit(app.exec_())