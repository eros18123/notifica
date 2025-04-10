import os
import sys
import logging
import random
import json
import time
import subprocess
import math
from aqt import mw, gui_hooks
from aqt.qt import QMenu, QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QCheckBox, QMessageBox, QAction
from PyQt6.QtCore import QTimer, Qt, QPointF
from PyQt6.QtGui import QIcon, QPainter, QPixmap, QColor, QFont, QBrush, QPen, QPainterPath
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QSystemTrayIcon, QWidget, QVBoxLayout, QFileDialog, QListWidget, QListWidgetItem, QScrollArea, QComboBox, QHBoxLayout, QGridLayout, QFrame, QTextEdit

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

ADDON_PATH = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(ADDON_PATH, "star_config.json")

class MessageImagePair:
    def __init__(self, message="", image_path=""):
        self.message = message
        self.image_path = image_path

class AnkiProgressHandler:
    def __init__(self):
        self.saved_due_card_count = 0
        self.tray_icon = None
        self.study_timer = QTimer()
        self.last_notification_time = 0
        self.is_in_review = False
        self.notification_paused = False
        self.load_settings()
        self.load_message_image_pairs()
        self.setup_tray()
        self.setup_study_reminder()
        self.add_menu_to_anki()
        gui_hooks.reviewer_did_show_question.append(self.on_enter_review)
        gui_hooks.reviewer_will_end.append(self.on_exit_review)
        gui_hooks.sync_did_finish.append(self.check_for_new_cards)
        gui_hooks.state_did_change.append(self.on_state_change)
        gui_hooks.reviewer_did_answer_card.append(lambda *args: self.check_for_new_cards())

    def load_settings(self):
        self.settings_file = os.path.join(ADDON_PATH, "settings.json")
        default_settings = {"notification_enabled": True, "notification_interval": 5, "selected_deck": "all"}
        self.settings = default_settings
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as file:
                    self.settings.update(json.load(file))
            except Exception as e:
                logger.error(f"Error loading settings: {e}")
        self.notification_enabled = self.settings["notification_enabled"]
        self.notification_interval = self.settings["notification_interval"]
        self.selected_deck = self.settings["selected_deck"]

    def save_settings(self):
        self.settings.update({
            "notification_enabled": self.notification_enabled,
            "notification_interval": self.notification_interval,
            "selected_deck": self.selected_deck
        })
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as file:
                json.dump(self.settings, file, indent=4)
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            
    def load_message_image_pairs(self):
        self.pairs_file = os.path.join(ADDON_PATH, "message_image_pairs.json")
        self.pairs = []
        if os.path.exists(self.pairs_file):
            try:
                with open(self.pairs_file, 'r', encoding='utf-8') as file:
                    pairs_data = json.load(file)
                    for pair in pairs_data:
                        self.pairs.append(MessageImagePair(pair.get("message", ""), pair.get("image_path", "")))
            except Exception as e:
                logger.error(f"Error loading message-image pairs: {e}")
                
        if not self.pairs:
            msg_file_path = os.path.join(ADDON_PATH, "msg.txt")
            if os.path.exists(msg_file_path):
                try:
                    with open(msg_file_path, 'r', encoding='utf-8') as file:
                        for line in file:
                            if line.strip():
                                self.pairs.append(MessageImagePair(line.strip(), ""))
                except Exception as e:
                    logger.error(f"Error loading messages from msg.txt: {e}")

    def save_message_image_pairs(self):
        pairs_data = []
        for pair in self.pairs:
            pairs_data.append({
                "message": pair.message,
                "image_path": pair.image_path
            })
        try:
            with open(self.pairs_file, 'w', encoding='utf-8') as file:
                json.dump(pairs_data, file, indent=4)
        except Exception as e:
            logger.error(f"Error saving message-image pairs: {e}")

    def get_deck_names(self):
        return ['all'] + sorted([d['name'] for d in mw.col.decks.all()]) if mw.col else ['all']

    def get_due_cards_count(self):
        if not mw.col:
            return 0
        try:
            if self.selected_deck == "all":
                return len(mw.col.find_cards("-is:buried (is:new or is:due)"))
            deck_name = self.selected_deck.replace("'", "\\'")
            return len(mw.col.find_cards(f"deck:\"{deck_name}\" -is:buried (is:new or is:due)"))
        except Exception as e:
            logger.error(f"Error counting cards: {e}")
            return 0

    def create_overlay_icon(self, count):
        svg = f'''<svg width="100" height="100" viewBox="0 0 100 100">
            <path fill="#ff0000" d="M50 5 L61.8 38.2 L95 38.2 L68.2 58.2 L79.1 90.5 L50 70 L20.9 90.5 L31.8 58.2 L5 38.2 L38.2 38.2Z"/>
            <text x="{50 if count < 10 else 45 if count < 100 else 40}" y="65" font-family="Arial" font-size="{45 if count < 10 else 35 if count < 100 else 25}" font-weight="bold" fill="white" text-anchor="middle">{count}</text>
        </svg>'''
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(bytearray(svg, encoding='utf-8'))
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(mw)
        icon_path = os.path.join(os.path.dirname(mw.pm.base), 'anki.ico')
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        self.tray_icon.activated.connect(self.tray_icon_clicked)
        self.tray_icon.show()

    def tray_icon_clicked(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.Context):
            self.update_progress()

    def add_menu_to_anki(self):
        self.menu = QMenu("Notifications", mw)
        mw.form.menubar.addMenu(self.menu)
        self.menu.aboutToShow.connect(self.show_settings_dialog)
        toggle_action = QAction("Show Notification", mw)
        toggle_action.triggered.connect(toggle_notification)
        self.menu.addAction(toggle_action)
        close_action = QAction("Close Notification", mw)
        close_action.triggered.connect(close_notification)
        self.menu.addAction(close_action)

    def show_settings_dialog(self):
        global dialog
        dialog = QDialog(None, Qt.WindowType.Window | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setWindowTitle("Configure Notifications")
        layout = QVBoxLayout()
        
        settings_group = QFrame()
        settings_layout = QGridLayout(settings_group)
        
        self.enable_checkbox = QCheckBox("Enable Notifications")
        self.enable_checkbox.setChecked(self.notification_enabled)
        settings_layout.addWidget(self.enable_checkbox, 0, 0, 1, 2)
        
        settings_layout.addWidget(QLabel("Notification Interval (minutes):"), 1, 0)
        self.interval_input = QLineEdit(str(self.notification_interval))
        settings_layout.addWidget(self.interval_input, 1, 1)
        
        settings_layout.addWidget(QLabel("Select Deck:"), 2, 0)
        self.deck_combo = QComboBox()
        self.deck_combo.addItems(self.get_deck_names())
        self.deck_combo.setCurrentText(self.selected_deck)
        settings_layout.addWidget(self.deck_combo, 2, 1)
        
        layout.addWidget(settings_group)
        
        message_group = QFrame()
        message_layout = QGridLayout(message_group)
        
        message_layout.addWidget(QLabel("Enter a new message:"), 0, 0)
        self.new_message_input = QLineEdit()
        message_layout.addWidget(self.new_message_input, 0, 1)
        
        message_layout.addWidget(QLabel("Add image:"), 1, 0)
        image_layout = QHBoxLayout()
        self.selected_image_label = QLabel("No image selected")
        image_layout.addWidget(self.selected_image_label, 1)
        self.select_image_button = QPushButton("Select")
        self.select_image_button.clicked.connect(self.select_image)
        image_layout.addWidget(self.select_image_button)
        message_layout.addLayout(image_layout, 1, 1)
        
        buttons_layout = QHBoxLayout()
        
        add_button = QPushButton("Add Message/Image")
        add_button.clicked.connect(self.add_message_image)
        buttons_layout.addWidget(add_button)
        
        show_all_button = QPushButton("Show All")
        show_all_button.clicked.connect(self.show_all_items)
        buttons_layout.addWidget(show_all_button)
        
        save_button = QPushButton("Save Settings")
        save_button.clicked.connect(lambda: self.save_settings_from_dialog(dialog))
        buttons_layout.addWidget(save_button)
        
        layout.addWidget(message_group)
        layout.addLayout(buttons_layout)
        
        dialog.setLayout(layout)
        dialog.show()

    def select_image(self):
        self.image_path, _ = QFileDialog.getOpenFileName(mw, "Select an image", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if self.image_path:
            self.selected_image_label.setText(os.path.basename(self.image_path))
        else:
            self.selected_image_label.setText("No image selected")

    def add_message_image(self):
        message = self.new_message_input.text().strip()
        image_path = getattr(self, 'image_path', "")
        
        if not message and not image_path:
            QMessageBox.warning(dialog, "Error", "Please enter a message or select an image (or both).")
            return
            
        if image_path:
            images_dir = os.path.join(ADDON_PATH, "imagens")
            os.makedirs(images_dir, exist_ok=True)
            image_name = os.path.basename(image_path)
            destination_path = os.path.join(images_dir, image_name)
            
            if not os.path.exists(destination_path):
                import shutil
                shutil.copy(image_path, destination_path)
                image_path = destination_path
            else:
                image_path = destination_path
        
        self.pairs.append(MessageImagePair(message, image_path))
        self.save_message_image_pairs()
        
        self.new_message_input.clear()
        self.selected_image_label.setText("No image selected")
        self.image_path = ""
        
    def show_all_items(self):
        if not self.pairs:
            QMessageBox.warning(dialog, "Warning", "No content found.")
            return
        
        global all_items_dialog
        all_items_dialog = QDialog(None, Qt.WindowType.Window | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowStaysOnTopHint)
        all_items_dialog.setWindowTitle("All Content")
        all_items_dialog.resize(800, 500)
        all_items_dialog.setStyleSheet("background-color: white; border: 1px solid black;")
        layout = QVBoxLayout()
        
        self.item_rows = []
        self.selected_items = []
        self.last_selected_index = -1
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(5, 5, 5, 5)
        header_layout.setSpacing(0)
        
        message_header = QLabel("<b>Message</b>")
        message_header.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
        message_header.setFixedWidth(650)
        message_header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        image_header = QLabel("<b>Image</b>")
        image_header.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
        image_header.setFixedWidth(120)
        image_header.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        
        header_layout.addWidget(message_header)
        header_layout.addWidget(image_header)
        
        content_layout.addLayout(header_layout)
        
        items_frame = QFrame()
        items_layout = QVBoxLayout(items_frame)
        items_layout.setSpacing(5)
        items_layout.setContentsMargins(0, 0, 0, 0)
        
        light_gray = "#e6e6e6"
        darker_gray = "#d0d0d0"
        
        images_dir = os.path.join(ADDON_PATH, "imagens")
        os.makedirs(images_dir, exist_ok=True)
        image_files = [f for f in os.listdir(images_dir) if os.path.isfile(os.path.join(images_dir, f)) and 
                      f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))]
        
        for i, pair in enumerate(self.pairs):
            row_widget = QWidget()
            
            if i % 2 == 0:
                row_widget.setStyleSheet(f"background-color: {light_gray}; border: 1px solid black; font-family: Arial; font-size: 16pt; color: black;")
            else:
                row_widget.setStyleSheet(f"background-color: {light_gray}; border: 1px solid black; font-family: Arial; font-size: 16pt; color: black;")
            
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(5, 5, 5, 5)
            row_layout.setSpacing(0)
            
            message_text = pair.message if pair.message else "(No message)"
            message_label = QLabel(message_text)
            message_label.setWordWrap(True)
            message_label.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
            message_label.setFixedWidth(650)
            
            image_widget = QLabel()
            image_widget.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
            
            image_path = None
            
            if pair.image_path and os.path.exists(pair.image_path):
                image_path = pair.image_path
            elif i < len(image_files):
                image_path = os.path.join(images_dir, image_files[i])
                pair.image_path = image_path
            
            if image_path and os.path.exists(image_path):
                pixmap = QPixmap(image_path).scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio)
                image_widget.setPixmap(pixmap)
            else:
                image_widget.setText("(No image)")
                
            image_widget.setFixedSize(120, 120)
            image_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            row_layout.addWidget(message_label)
            row_layout.addWidget(image_widget)
            
            row_widget.setProperty("item_index", i)
            
            def create_click_handler(idx):
                return lambda evt: self.select_item_in_dialog(idx, self.item_rows, evt)
                
            row_widget.mousePressEvent = create_click_handler(i)
            
            self.item_rows.append(row_widget)
            items_layout.addWidget(row_widget)
        
        self.save_message_image_pairs()
        
        content_layout.addWidget(items_frame)
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)
        
        self.selection_status = QLabel("No items selected")
        self.selection_status.setStyleSheet("font-family: Arial; font-size: 14pt; color: black;")
        layout.addWidget(self.selection_status)
        
        buttons_layout = QHBoxLayout()
        button_style = "font-family: Arial; font-size: 16pt; color: black; background-color: #f0f0f0;"
        
        edit_message_button = QPushButton("Edit Message")
        edit_message_button.setStyleSheet(button_style)
        edit_message_button.clicked.connect(lambda: self.edit_selected_message_in_dialog())
        buttons_layout.addWidget(edit_message_button)
        
        view_image_button = QPushButton("View Image")
        view_image_button.setStyleSheet(button_style)
        view_image_button.clicked.connect(lambda: self.view_selected_image_in_dialog())
        buttons_layout.addWidget(view_image_button)
        
        edit_image_button = QPushButton("Edit Image")
        edit_image_button.setStyleSheet(button_style)
        edit_image_button.clicked.connect(lambda: self.edit_selected_image_in_dialog())
        buttons_layout.addWidget(edit_image_button)
        
        remove_item_button = QPushButton("Remove Selected Items")
        remove_item_button.setStyleSheet(button_style)
        remove_item_button.clicked.connect(lambda: self.remove_selected_items_in_dialog())
        buttons_layout.addWidget(remove_item_button)
        
        layout.addLayout(buttons_layout)
        all_items_dialog.setLayout(layout)
        all_items_dialog.show()

    def select_item_in_dialog(self, index, row_widgets, event):
        light_gray = "#e6e6e6"
        darker_gray = "#d0d0d0"
        selected_color = "#d0d0ff"
        
        ctrl_pressed = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        
        if not ctrl_pressed:
            self.selected_items = []
            for i, row in enumerate(row_widgets):
                if i % 2 == 0:
                    row.setStyleSheet(f"background-color: {light_gray}; border: 1px solid black; font-family: Arial; font-size: 16pt; color: black;")
                else:
                    row.setStyleSheet(f"background-color: {light_gray}; border: 1px solid black; font-family: Arial; font-size: 16pt; color: black;")
        
        if ctrl_pressed and index in self.selected_items:
            self.selected_items.remove(index)
            if index % 2 == 0:
                row_widgets[index].setStyleSheet(f"background-color: {light_gray}; border: 1px solid black; font-family: Arial; font-size: 16pt; color: black;")
            else:
                row_widgets[index].setStyleSheet(f"background-color: {light_gray}; border: 1px solid black; font-family: Arial; font-size: 16pt; color: black;")
        else:
            if index not in self.selected_items:
                self.selected_items.append(index)
            row_widgets[index].setStyleSheet(f"background-color: {selected_color}; border: 1px solid black; font-family: Arial; font-size: 16pt; color: black;")
        
        self.last_selected_index = index
        
        if len(self.selected_items) == 0:
            self.selection_status.setText("No items selected")
        elif len(self.selected_items) == 1:
            self.selection_status.setText(f"1 item selected")
        else:
            self.selection_status.setText(f"{len(self.selected_items)} items selected")

    def edit_selected_message_in_dialog(self):
        if self.last_selected_index < 0 or not self.selected_items:
            QMessageBox.warning(dialog, "Warning", "No message selected.")
            return
            
        pair = self.pairs[self.last_selected_index]
        
        dialog = QDialog(mw, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setWindowTitle("Edit Message")
        dialog.setStyleSheet("background-color: #d3d3d3;")
        dialog.resize(600, 500)
        layout = QVBoxLayout()
        
        label = QLabel("Edit message:")
        label.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
        layout.addWidget(label)
        
        message_input = QTextEdit(pair.message)
        message_input.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
        message_input.setMinimumHeight(200)
        layout.addWidget(message_input)
        
        button_layout = QHBoxLayout()
        cancel_button = QPushButton("Cancel")
        cancel_button.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button)
        
        save_button = QPushButton("Save")
        save_button.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
        save_button.clicked.connect(dialog.accept)
        button_layout.addWidget(save_button)
        
        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_message = message_input.toPlainText().strip()
            self.pairs[self.last_selected_index].message = new_message
            self.save_message_image_pairs()
            
            current_row = self.item_rows[self.last_selected_index]
            message_label = current_row.layout().itemAt(0).widget()
            display_message = new_message if new_message else "(No message)"
            message_label.setText(display_message)

    def view_selected_image_in_dialog(self):
        if self.last_selected_index < 0 or not self.selected_items:
            QMessageBox.warning(dialog, "Warning", "No image selected.")
            return
            
        pair = self.pairs[self.last_selected_index]
        if not pair.image_path or not os.path.exists(pair.image_path):
            QMessageBox.warning(dialog, "Warning", "No image associated with this item.")
            return
            
        dialog = QDialog(mw, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setWindowTitle("Image View")
        dialog.setStyleSheet("background-color: #d3d3d3;")
        layout = QVBoxLayout()
        
        if pair.message:
            message_label = QLabel(pair.message)
            message_label.setWordWrap(True)
            message_label.setStyleSheet("font-family: Arial; font-size: 16pt; color: black;")
            layout.addWidget(message_label)
        
        pixmap = QPixmap(pair.image_path).scaled(400, 400, Qt.AspectRatioMode.KeepAspectRatio)
        image_label = QLabel()
        image_label.setPixmap(pixmap)
        layout.addWidget(image_label)
        
        dialog.setLayout(layout)
        dialog.exec()
    
    def edit_selected_image_in_dialog(self):
        if self.last_selected_index < 0 or not self.selected_items:
            QMessageBox.warning(dialog, "Warning", "No image selected.")
            return
            
        pair = self.pairs[self.last_selected_index]
        
        image_path, _ = QFileDialog.getOpenFileName(mw, "Select an image", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if not image_path:
            return
            
        images_dir = os.path.join(ADDON_PATH, "imagens")
        os.makedirs(images_dir, exist_ok=True)
        image_name = os.path.basename(image_path)
        destination_path = os.path.join(images_dir, image_name)
        
        if image_path != destination_path:
            import shutil
            shutil.copy(image_path, destination_path)
        
        self.pairs[self.last_selected_index].image_path = destination_path
        self.save_message_image_pairs()
        
        current_row = self.item_rows[self.last_selected_index]
        image_label = current_row.layout().itemAt(1).widget()
        pixmap = QPixmap(destination_path).scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio)
        image_label.setPixmap(pixmap)
        image_label.setText("")
    
    def remove_selected_items_in_dialog(self):
        if not self.selected_items:
            QMessageBox.warning(dialog, "Warning", "No items selected.")
            return
        
        if len(self.selected_items) == 1:
            confirm_message = "Are you sure you want to remove this item?"
        else:
            confirm_message = f"Are you sure you want to remove {len(self.selected_items)} items?"
            
        confirm = QMessageBox.question(dialog, "Confirm", confirm_message, 
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            indices_to_remove = sorted(self.selected_items, reverse=True)
            
            for index in indices_to_remove:
                self.pairs.pop(index)
            
            self.save_message_image_pairs()
            
            if len(self.selected_items) == 1:
                QMessageBox.information(dialog, "Success", "Item removed successfully!")
            else:
                QMessageBox.information(dialog, "Success", f"{len(self.selected_items)} items removed successfully!")
                
            self.show_all_items()

    def save_settings_from_dialog(self, dialog):
        try:
            interval = int(self.interval_input.text())
            if interval <= 0:
                raise ValueError("Interval must be greater than 0.")
            self.notification_interval = interval
            self.notification_enabled = self.enable_checkbox.isChecked()
            self.selected_deck = self.deck_combo.currentText()
            self.save_settings()
            self.setup_study_reminder()
            self.update_progress()
            
            close_notification()
            if self.notification_enabled:
                QTimer.singleShot(1000, start_notification_process)
                
            dialog.close()
        except ValueError as e:
            QMessageBox.warning(dialog, "Error", str(e))

    def setup_study_reminder(self):
        self.study_timer.stop()
        try:
            self.study_timer.timeout.disconnect()
        except TypeError:
            pass
        if self.notification_enabled and not self.is_in_review:
            interval_ms = self.notification_interval * 60 * 1000
            self.study_timer.timeout.connect(self.check_and_show_reminder)
            self.study_timer.start(interval_ms)

    def check_and_show_reminder(self):
        if not self.is_in_review and not self.notification_paused and self.get_due_cards_count() > 0:
            toggle_notification()

    def check_for_new_cards(self):
        if not mw.col:
            return
        current_due_cards = self.get_due_cards_count()
        if current_due_cards != self.saved_due_card_count:
            self.update_progress()

    def on_state_change(self, state, *args):
        self.check_for_new_cards()

    def on_enter_review(self, card):
        current_deck = mw.col.decks.name(card.did)
        if current_deck == self.selected_deck or self.selected_deck == "all":
            self.is_in_review = True
            self.notification_paused = True
            self.study_timer.stop()
            save_state(True, True)
            
    def on_exit_review(self):
        if mw.col.decks.current():
            current_deck = mw.col.decks.current()['name']
            if current_deck == self.selected_deck or self.selected_deck == "all":
                self.is_in_review = False
                self.notification_paused = False
                self.setup_study_reminder()
                save_state(True, False)
        self.update_progress()

    def update_progress(self, *args):
        if not mw.col:
            return
        due_card_count = self.get_due_cards_count()
        if due_card_count > 0:
            overlay_icon = self.create_overlay_icon(due_card_count)
            mw.setWindowIcon(overlay_icon)
            if self.tray_icon:
                self.tray_icon.setIcon(overlay_icon)
        else:
            default_icon = QIcon(os.path.join(os.path.dirname(mw.pm.base), 'anki.ico'))
            mw.setWindowIcon(default_icon)
            if self.tray_icon:
                self.tray_icon.setIcon(default_icon)
        deck_info = f" - {self.selected_deck}" if self.selected_deck != "all" else ""
        mw.setWindowTitle(f"Anki ({due_card_count}){deck_info}" if due_card_count > 0 else "Anki")
        self.saved_due_card_count = due_card_count
        
        try:
            info = {
                "count": due_card_count,
                "deck": self.selected_deck
            }
            with open(os.path.join(ADDON_PATH, "card_count.json"), "w", encoding='utf-8') as f:
                json.dump(info, f)
        except Exception as e:
            logger.error(f"Error saving card count: {e}")

def save_state(active, in_review=False):
    try:
        with open(CONFIG_PATH, "w", encoding='utf-8') as f:
            json.dump({"active": active, "in_review": in_review}, f)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

def start_notification_process():
    script_path = os.path.join(ADDON_PATH, "star_notification_bg.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write("""
import sys
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QMenu, QSystemTrayIcon, QHBoxLayout
from PyQt6.QtCore import QTimer, Qt, QPointF
from PyQt6.QtGui import QPainter, QPainterPath, QBrush, QColor, QFont, QPixmap, QPen, QIcon
import os
import json
import random
import math

ADDON_PATH = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(ADDON_PATH, "star_config.json")
SETTINGS_PATH = os.path.join(ADDON_PATH, "settings.json")
PAIRS_PATH = os.path.join(ADDON_PATH, "message_image_pairs.json")
IMAGES_DIR = os.path.join(ADDON_PATH, "imagens")
CARD_COUNT_PATH = os.path.join(ADDON_PATH, "card_count.json")

class StarNotification(QWidget):
    def __init__(self):
        super().__init__()
        try:
            with open(CONFIG_PATH, "r", encoding='utf-8') as f:
                config = json.load(f)
                if config.get("in_review", False):
                    self.setVisible(False)
                    self.cycle_timer = QTimer(self)
                    self.check_timer = QTimer(self)
                    self.check_timer.timeout.connect(self.check_status)
                    self.check_timer.start(1000)
                    return
        except Exception as e:
            print(f"Error checking initial review state: {e}")

        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.tray_icon = None
        self.blink_state = True
        self.setupUI()
        self.setup_tray()
        self.load_settings()
        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.timeout.connect(self.start_blinking)
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.toggle_blink)
        self.blink_end_timer = QTimer(self)
        self.blink_end_timer.setSingleShot(True)
        self.blink_end_timer.timeout.connect(self.hide_notification)
        self.cycle_timer = QTimer(self)
        self.cycle_timer.timeout.connect(self.show_notification)
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.check_status)
        self.check_timer.start(1000)
        self.start_notification_cycle()

    def setupUI(self):
        self.setFixedSize(400, 150)
        self.setStyleSheet("background-color: transparent;")
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        
        self.content_widget = QWidget(self)
        self.content_widget.setFixedSize(400, 150)
        self.content_widget.setStyleSheet("background-color: white; border: 5px solid red;")
        self.content_layout = QHBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(5, 5, 5, 5)
        self.content_layout.setSpacing(5)
        
        self.star_pixmap = self.create_star_pixmap(30, 30)
        self.star_label = QLabel(self.content_widget)
        self.star_label.setPixmap(self.star_pixmap)
        self.star_label.setStyleSheet("border: none; background-color: transparent;")
        self.content_layout.addWidget(self.star_label)
        
        self.image_label = QLabel(self.content_widget)
        self.image_label.setFixedSize(100, 100)
        self.image_label.setStyleSheet("border: none; background-color: transparent;")
        self.content_layout.addWidget(self.image_label)
        
        self.text_label = QLabel(self.content_widget)
        self.text_label.setWordWrap(True)
        self.text_label.setStyleSheet("color: black; font: bold 12px Arial; border: none; background-color: transparent;")
        self.content_layout.addWidget(self.text_label)
        
        self.layout.addWidget(self.content_widget)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.position_near_clock()

    def start_notification_cycle(self):
        self.cycle_timer.start(self.notification_interval)
        self.show_notification()

    def show_notification(self):
        self.update_content()
        self.show()
        self.raise_()
        self.setVisible(True)
        self.blink_state = True
        self.show_timer.start(5000)

    def start_blinking(self):
        self.blink_timer.start(500)
        self.blink_end_timer.start(2000)

    def toggle_blink(self):
        self.blink_state = not self.blink_state
        self.setVisible(self.blink_state)

    def hide_notification(self):
        self.blink_timer.stop()
        self.setVisible(True)
        self.hide()
        self.tray_icon.show()

    def create_star_pixmap(self, width, height):
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        center = QPointF(width / 2, height / 2)
        radius_outer = min(width, height) / 2
        radius_inner = radius_outer * 0.4
        for i in range(5):
            angle = i * 2 * 3.14159 / 5 - 3.14159 / 2
            angle_inner = angle + 3.14159 / 5
            x_outer = center.x() + radius_outer * math.cos(angle)
            y_outer = center.y() + radius_outer * math.sin(angle)
            x_inner = center.x() + radius_inner * math.cos(angle_inner)
            y_inner = center.y() + radius_inner * math.sin(angle_inner)
            if i == 0:
                path.moveTo(x_outer, y_outer)
            else:
                path.lineTo(x_outer, y_outer)
            path.lineTo(x_inner, y_inner)
        path.closeSubpath()
        painter.setPen(Qt.GlobalColor.red)
        painter.setBrush(QBrush(QColor(255, 0, 0, 180)))
        painter.drawPath(path)
        painter.end()
        return pixmap

    def load_settings(self):
        default_settings = {"notification_interval": 5}
        self.settings = default_settings
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, 'r', encoding='utf-8') as file:
                    self.settings.update(json.load(file))
            except Exception as e:
                print(f"Error loading settings: {e}")
        self.notification_interval = self.settings["notification_interval"] * 60 * 1000
        
        self.pairs = []
        if os.path.exists(PAIRS_PATH):
            try:
                with open(PAIRS_PATH, 'r', encoding='utf-8') as file:
                    self.pairs = json.load(file)
            except Exception as e:
                print(f"Error loading message-image pairs: {e}")
        
        if not self.pairs:
            self.pairs = []
            msg_file = os.path.join(ADDON_PATH, "msg.txt")
            if os.path.exists(msg_file):
                try:
                    with open(msg_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                self.pairs.append({"message": line.strip(), "image_path": ""})
                except Exception as e:
                    print(f"Error loading messages from msg.txt: {e}")

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(QIcon(self.star_pixmap), self)
        self.tray_icon.setContextMenu(self.create_tray_menu())
        self.tray_icon.show()
        self.tray_icon.activated.connect(self.tray_icon_clicked)

    def create_tray_menu(self):
        menu = QMenu()
        close_action = menu.addAction("Close")
        close_action.triggered.connect(self.close_notification)
        return menu

    def tray_icon_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_notification()

    def update_content(self):
        # Load card count from card_count.json
        due_cards = 0
        selected_deck = "all"
        try:
            with open(CARD_COUNT_PATH, 'r', encoding='utf-8') as f:
                card_info = json.load(f)
                due_cards = card_info.get("count", 0)
                selected_deck = card_info.get("deck", "all")
        except Exception as e:
            print(f"Error loading card count: {e}")

        # Only show notification if there are due cards
        if due_cards == 0:
            self.hide()
            return

        if not self.pairs:
            self.text_label.setText("No content available")
            self.image_label.clear()
            return
            
        pair = random.choice(self.pairs)
        
        message = pair.get("message", "")
        if not message:
            message = "(No message)"
            
        # Add card count to the message
        deck_text = f" no deck {selected_deck}" if selected_deck != "all" else ""
        card_status = f"Faltam {due_cards} cards{deck_text}!"
        full_message = f"{message}<br><br><b>{card_status}</b>"
        
        self.text_label.setText(full_message)
        
        image_path = pair.get("image_path", "")
        if image_path and os.path.exists(image_path):
            pixmap = QPixmap(image_path).scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio)
            self.image_label.setPixmap(pixmap)
        else:
            self.image_label.clear()

    def position_near_clock(self):
        screen_rect = QApplication.primaryScreen().availableGeometry()
        self.move(screen_rect.width() - self.width() - 10, screen_rect.height() - self.height() - 40)

    def show_context_menu(self, position):
        menu = QMenu()
        close_action = menu.addAction("Close Notification")
        close_action.triggered.connect(self.close_notification)
        menu.exec(self.mapToGlobal(position))

    def close_notification(self):
        with open(CONFIG_PATH, "w", encoding='utf-8') as f:
            json.dump({"active": False}, f)
        self.tray_icon.hide()
        self.close()
        QApplication.quit()

    def check_status(self):
        try:
            with open(CONFIG_PATH, "r", encoding='utf-8') as f:
                config = json.load(f)
                if not config.get("active", True):
                    self.tray_icon.hide()
                    self.close()
                    QApplication.quit()
                    return
                    
                if config.get("in_review", False):
                    self.cycle_timer.stop()
                    self.blink_timer.stop()
                    self.show_timer.stop()
                    if self.isVisible():
                        self.hide()
                    return
                else:
                    if not self.cycle_timer.isActive():
                        self.cycle_timer.start(self.notification_interval)
        except Exception as e:
            print(f"Error checking status: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = StarNotification()
    sys.exit(app.exec())
""")
    save_state(True)
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["pythonw", script_path], creationflags=creationflags)
    else:
        subprocess.Popen(["python3", script_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

def toggle_notification():
    try:
        with open(CONFIG_PATH, "r", encoding='utf-8') as f:
            config = json.load(f)
            is_active = config.get("active", False)
    except:
        is_active = False
    if not is_active:
        start_notification_process()

def close_notification():
    save_state(False)
    QMessageBox.information(dialog, "Info", "Notification stopped (it will close shortly)")

def initialize_handler():
    global handler
    save_state(False)
    time.sleep(2)
    handler = AnkiProgressHandler()
    gui_hooks.collection_did_load.append(lambda args: handler.update_progress())
    gui_hooks.reviewer_did_show_question.append(lambda args: handler.update_progress())
    gui_hooks.reviewer_did_answer_card.append(lambda *args: handler.update_progress())
    gui_hooks.reviewer_will_end.append(handler.update_progress)
    gui_hooks.sync_did_finish.append(handler.update_progress)
    start_notification_process()

if not os.path.exists(CONFIG_PATH):
    save_state(True)

gui_hooks.profile_did_open.append(initialize_handler)
