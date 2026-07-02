#!/usr/bin/env python3
"""
Shake Test & MPU (ST & MPU) settings tab.

A self-contained QWidget meant to be added as one tab in RoboCam's main
QTabWidget (alongside Setup, Calibration, Experiment, Manual Control, etc).

Since the shake test and MPU logging are two independent, separately
toggleable features (per our earlier discussion), this tab keeps their
settings in two separate sections with two separate getters:

    Shake Test Settings        -> get_shake_settings() -> ShakeSettings
        shake_duration, jerk, distance, acceleration, velocity

    MPU Data Settings          -> get_mpu_settings()   -> MPUSettings
        enabled, afs_sel

AFS_SEL (the MPU accelerometer range) lives only in the MPU section now,
since it configures the sensor, not the shake motion.

Usage once wired into RoboCam's main window:

    from shake_settings_tab import STAndMPUSettingsTab

    self.st_mpu_tab = STAndMPUSettingsTab()
    self.tabs.addTab(self.st_mpu_tab, "ST & MPU")

    # Whenever the shake worker is about to run:
    try:
        shake_settings = self.st_mpu_tab.get_shake_settings()
    except ValueError as e:
        QMessageBox.critical(self, "Shake Settings Error", str(e))
        return

    # Whenever the MPU logging worker is about to run (independently):
    try:
        mpu_settings = self.st_mpu_tab.get_mpu_settings()
    except ValueError as e:
        QMessageBox.critical(self, "MPU Settings Error", str(e))
        return
    if not mpu_settings.enabled:
        # user hasn't turned MPU logging on
        ...
"""

import sys
from dataclasses import dataclass

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QGridLayout, QVBoxLayout,
    QGroupBox, QPushButton, QCheckBox, QMessageBox,
)

# =========================
# Defaults
# =========================

# -- Shake test --
DEFAULT_SHAKE_DURATION = 20.0      # seconds
DEFAULT_JERK = 20.0                # mm/s
DEFAULT_DISTANCE = 4.0             # mm, total peak-to-peak Y travel
DEFAULT_ACCELERATION = 2000.0      # mm/s^2
DEFAULT_VELOCITY = 220.0           # mm/s

# -- MPU --
DEFAULT_MPU_ENABLED = False
DEFAULT_AFS_SEL = 1                # 0=+-2g, 1=+-4g, 2=+-8g, 3=+-16g


@dataclass
class ShakeSettings:
    shake_duration: float = DEFAULT_SHAKE_DURATION
    jerk: float = DEFAULT_JERK
    distance: float = DEFAULT_DISTANCE
    acceleration: float = DEFAULT_ACCELERATION
    velocity: float = DEFAULT_VELOCITY


@dataclass
class MPUSettings:
    enabled: bool = DEFAULT_MPU_ENABLED
    afs_sel: int = DEFAULT_AFS_SEL


class STAndMPUSettingsTab(QWidget):
    """Tab for editing Shake Test settings and MPU data-logging settings.
    The two sections are independent -- neither depends on the other being
    enabled or configured. Values are read live via get_shake_settings() /
    get_mpu_settings(); whatever triggers each feature calls the relevant
    getter right before running.
    """

    settingsChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shake_entries = {}
        self.mpu_entries = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)

        # -------- Shake Test section --------
        shake_box = QGroupBox("Shake Test Settings")
        shake_grid = QGridLayout(shake_box)

        shake_fields = [
            ("shake_duration", "Shake Duration (s)", DEFAULT_SHAKE_DURATION),
            ("jerk", "Jerk (mm/s)", DEFAULT_JERK),
            ("distance", "Distance (mm)", DEFAULT_DISTANCE),
            ("acceleration", "Acceleration (mm/s^2)", DEFAULT_ACCELERATION),
            ("velocity", "Velocity (mm/s)", DEFAULT_VELOCITY),
        ]
        for i, (key, label, value) in enumerate(shake_fields):
            row = i // 2
            col = (i % 2) * 2
            shake_grid.addWidget(QLabel(label), row, col)
            edit = QLineEdit(str(value))
            edit.setFixedWidth(100)
            edit.editingFinished.connect(self.settingsChanged.emit)
            shake_grid.addWidget(edit, row, col + 1)
            self.shake_entries[key] = edit

        shake_reset_btn = QPushButton("Reset to Defaults")
        shake_reset_btn.clicked.connect(self.reset_shake_defaults)
        shake_grid.addWidget(shake_reset_btn, 3, 0, 1, 2)

        outer.addWidget(shake_box)

        # -------- MPU Data section (separate, independent feature) --------
        mpu_box = QGroupBox("MPU Data Settings")
        mpu_layout = QVBoxLayout(mpu_box)

        self.mpu_enabled_checkbox = QCheckBox("Enable MPU Data Logging")
        self.mpu_enabled_checkbox.setChecked(DEFAULT_MPU_ENABLED)
        self.mpu_enabled_checkbox.stateChanged.connect(self.settingsChanged.emit)
        mpu_layout.addWidget(self.mpu_enabled_checkbox)

        mpu_grid = QGridLayout()
        mpu_grid.addWidget(QLabel("MPU Range / AFS_SEL"), 0, 0)
        afs_edit = QLineEdit(str(DEFAULT_AFS_SEL))
        afs_edit.setFixedWidth(100)
        afs_edit.editingFinished.connect(self.settingsChanged.emit)
        mpu_grid.addWidget(afs_edit, 0, 1)
        self.mpu_entries["afs_sel"] = afs_edit

        note = QLabel("MPU range key: 0=+-2g, 1=+-4g, 2=+-8g, 3=+-16g")
        mpu_grid.addWidget(note, 1, 0, 1, 2)
        mpu_layout.addLayout(mpu_grid)

        mpu_reset_btn = QPushButton("Reset to Defaults")
        mpu_reset_btn.clicked.connect(self.reset_mpu_defaults)
        mpu_layout.addWidget(mpu_reset_btn)

        outer.addWidget(mpu_box)
        outer.addStretch(1)

    # ---------- reset helpers ----------

    def reset_shake_defaults(self):
        defaults = ShakeSettings()
        self.shake_entries["shake_duration"].setText(str(defaults.shake_duration))
        self.shake_entries["jerk"].setText(str(defaults.jerk))
        self.shake_entries["distance"].setText(str(defaults.distance))
        self.shake_entries["acceleration"].setText(str(defaults.acceleration))
        self.shake_entries["velocity"].setText(str(defaults.velocity))
        self.settingsChanged.emit()

    def reset_mpu_defaults(self):
        defaults = MPUSettings()
        self.mpu_enabled_checkbox.setChecked(defaults.enabled)
        self.mpu_entries["afs_sel"].setText(str(defaults.afs_sel))
        self.settingsChanged.emit()

    # ---------- getters ----------

    def get_shake_settings(self) -> ShakeSettings:
        """Validate and return the current shake test settings. Raises
        ValueError with a human-readable message on bad input."""
        settings = ShakeSettings()
        try:
            settings.shake_duration = float(self.shake_entries["shake_duration"].text())
            settings.jerk = float(self.shake_entries["jerk"].text())
            settings.distance = float(self.shake_entries["distance"].text())
            settings.acceleration = float(self.shake_entries["acceleration"].text())
            settings.velocity = float(self.shake_entries["velocity"].text())
        except ValueError:
            raise ValueError("All shake test settings must be valid numbers.")

        if settings.shake_duration <= 0:
            raise ValueError("Shake Duration must be greater than 0.")
        if settings.distance <= 0:
            raise ValueError("Distance must be greater than 0.")
        if settings.velocity <= 0:
            raise ValueError("Velocity must be greater than 0.")
        if settings.acceleration <= 0:
            raise ValueError("Acceleration must be greater than 0.")
        if settings.jerk < 0:
            raise ValueError("Jerk cannot be negative.")
        return settings

    def get_mpu_settings(self) -> MPUSettings:
        """Validate and return the current MPU data-logging settings. Raises
        ValueError with a human-readable message on bad input."""
        settings = MPUSettings()
        settings.enabled = self.mpu_enabled_checkbox.isChecked()
        try:
            settings.afs_sel = int(self.mpu_entries["afs_sel"].text())
        except ValueError:
            raise ValueError("MPU Range / AFS_SEL must be a whole number.")

        if settings.afs_sel not in (0, 1, 2, 3):
            raise ValueError("MPU Range / AFS_SEL must be 0, 1, 2, or 3.")
        return settings


# ---- standalone demo / smoke test ----
if __name__ == "__main__":
    from PyQt6.QtWidgets import QMainWindow

    class DemoWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("ST & MPU Tab Demo")
            self.resize(520, 380)
            self.tab = STAndMPUSettingsTab()
            self.setCentralWidget(self.tab)

            btn = QPushButton("Print current settings", self)
            btn.move(10, 330)
            btn.clicked.connect(self.print_settings)

        def print_settings(self):
            try:
                shake = self.tab.get_shake_settings()
                print("Shake:", shake)
            except ValueError as e:
                QMessageBox.critical(self, "Shake Settings Error", str(e))
                return
            try:
                mpu = self.tab.get_mpu_settings()
                print("MPU:", mpu)
            except ValueError as e:
                QMessageBox.critical(self, "MPU Settings Error", str(e))

    app = QApplication(sys.argv)
    win = DemoWindow()
    win.show()
    sys.exit(app.exec())