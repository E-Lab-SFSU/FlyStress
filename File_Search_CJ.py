"""
File Search
Purpose: on multiple operating systems(OS), be able to search for directories (folders) and access different specified file types files.
Author: Cherese Jordan

Features:
- Small GUI
- Search for folders
- toggle what file type you want
    [".csv", ".m", ".py", ".txt", ".log"]
- search by name
"""
import sys
import csv
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog,
    QCheckBox, QComboBox, QTableWidget, QTableWidgetItem,
    QMenu
)


FILE_TYPES = {
    "CSV files (*.csv)": [".csv"],
    "MATLAB files (*.m)": [".m"],
    "CSV + MATLAB (*.csv, *.m)": [".csv", ".m"],
    "Python files (*.py)": [".py"],
    "Text files (*.txt, *.log)": [".txt", ".log"],
    "All supported files": [".csv", ".m", ".py", ".txt", ".log"]
}


def parse_line_ranges(text):
    """
    Accepts:
        10
        10-20
        10,15,22
        10-20,35,40-45
    Returns a set of line numbers.
    """
    text = text.strip()

    if not text:
        return set()

    lines = set()

    for part in text.replace(" ", "").split(","):
        if not part:
            continue

        if "-" in part:
            start, end = part.split("-", 1)
            start = int(start)
            end = int(end)

            if start > end:
                start, end = end, start

            lines.update(range(start, end + 1))
        else:
            lines.add(int(part))

    return lines


class FileSearchWorker(QThread):
    batch_found = pyqtSignal(list)
    finished_search = pyqtSignal()
    error_found = pyqtSignal(str)

    def __init__(
            self,
            folder_path,
            file_path,
            extensions,
            search_term,
            case_sensitive,
            wanted_lines
    ):
        super().__init__()

        self.folder_path = folder_path.strip()
        self.file_path = file_path.strip()
        self.extensions = extensions
        self.search_term = search_term
        self.case_sensitive = case_sensitive
        self.wanted_lines = wanted_lines
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True

    def run(self):
        batch = []

        try:
            files = self.get_files()

            if not files:
                self.batch_found.emit([
                    ["NONE", "", "", "No matching files found.", ""]
                ])
                self.finished_search.emit()
                return

            for file_path in files:
                if self.stop_requested:
                    break

                try:
                    if self.search_term:
                        self.search_file(file_path, batch)
                    elif self.wanted_lines:
                        self.get_specific_lines(file_path, batch)
                    else:
                        batch.append([
                            file_path.name,
                            file_path.suffix,
                            "",
                            "File found",
                            str(file_path)
                        ])

                    if len(batch) >= 100:
                        self.batch_found.emit(batch)
                        batch = []

                except Exception as e:
                    batch.append([
                        file_path.name,
                        file_path.suffix,
                        "",
                        f"Could not read file: {e}",
                        str(file_path)
                    ])

            if batch:
                self.batch_found.emit(batch)

        except Exception as e:
            self.error_found.emit(str(e))

        self.finished_search.emit()

    def get_files(self):
        files = []

        if self.file_path:
            path = Path(self.file_path).expanduser()

            if path.is_file():
                if not self.extensions or path.suffix.lower() in self.extensions:
                    files.append(path)

            return files

        if self.folder_path:
            folder = Path(self.folder_path).expanduser()

            for path in folder.rglob("*"):
                if self.stop_requested:
                    break

                try:
                    if path.is_symlink():
                        continue

                    if not path.is_file():
                        continue

                    if path.suffix.lower() not in self.extensions:
                        continue

                    files.append(path)

                except Exception:
                    continue

        return files

    def search_file(self, file_path, batch):
        if file_path.suffix.lower() == ".csv":
            self.search_csv_file(file_path, batch)
        else:
            self.search_text_file(file_path, batch)

    def search_csv_file(self, file_path, batch):
        with open(file_path, "r", encoding="utf-8", errors="replace", newline="") as file:
            reader = csv.reader(file)

            for row_number, row in enumerate(reader, start=1):
                if self.stop_requested:
                    return

                if self.wanted_lines and row_number not in self.wanted_lines:
                    continue

                row_text = " ".join(row)

                if self.is_match(row_text):
                    batch.append([
                        file_path.name,
                        file_path.suffix,
                        f"Row {row_number}",
                        row_text[:500],
                        str(file_path)
                    ])

    def search_text_file(self, file_path, batch):
        with open(file_path, "r", encoding="utf-8", errors="replace") as file:
            for line_number, line in enumerate(file, start=1):
                if self.stop_requested:
                    return

                if self.wanted_lines and line_number not in self.wanted_lines:
                    continue

                line_text = line.rstrip()

                if self.is_match(line_text):
                    batch.append([
                        file_path.name,
                        file_path.suffix,
                        f"Line {line_number}",
                        line_text[:500],
                        str(file_path)
                    ])

    def get_specific_lines(self, file_path, batch):
        with open(file_path, "r", encoding="utf-8", errors="replace", newline="") as file:
            for line_number, line in enumerate(file, start=1):
                if self.stop_requested:
                    return

                if line_number in self.wanted_lines:
                    label = "Row" if file_path.suffix.lower() == ".csv" else "Line"

                    batch.append([
                        file_path.name,
                        file_path.suffix,
                        f"{label} {line_number}",
                        line.rstrip()[:500],
                        str(file_path)
                    ])

    def is_match(self, text):
        if self.case_sensitive:
            return self.search_term in text

        return self.search_term.lower() in text.lower()


class ProjectFileSearchApp(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("File Search")
        self.resize(1000, 600)

        self.folder_path = ""
        self.file_path = ""
        self.worker = None

        layout = QVBoxLayout()

        folder_layout = QHBoxLayout()

        self.folder_label = QLabel("No folder selected")
        self.folder_button = QPushButton("Select Folder")
        self.folder_button.clicked.connect(self.select_folder)

        self.clear_folder_button = QPushButton("Clear Folder")
        self.clear_folder_button.clicked.connect(self.clear_folder)

        folder_layout.addWidget(self.folder_label)
        folder_layout.addWidget(self.folder_button)
        folder_layout.addWidget(self.clear_folder_button)

        file_layout = QHBoxLayout()

        self.file_label = QLabel("No file selected")
        self.file_button = QPushButton("Select File")
        self.file_button.clicked.connect(self.select_file)

        self.clear_file_button = QPushButton("Clear File")
        self.clear_file_button.clicked.connect(self.clear_file)

        file_layout.addWidget(self.file_label)
        file_layout.addWidget(self.file_button)
        file_layout.addWidget(self.clear_file_button)

        search_layout = QHBoxLayout()

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search text, or leave blank to list files")

        self.file_type_dropdown = QComboBox()
        self.file_type_dropdown.addItems(FILE_TYPES.keys())
        self.file_type_dropdown.setCurrentText("Python files (*.py)")

        search_layout.addWidget(self.search_box)
        search_layout.addWidget(self.file_type_dropdown)

        line_layout = QHBoxLayout()

        self.line_range_box = QLineEdit()
        self.line_range_box.setPlaceholderText("Optional lines/rows, e.g. 10, 15-20, 35")

        line_layout.addWidget(self.line_range_box)

        self.case_checkbox = QCheckBox("Case sensitive")

        button_layout = QHBoxLayout()

        self.search_button = QPushButton("Search / List / Get Lines")
        self.search_button.clicked.connect(self.search_files)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_search)
        self.stop_button.setEnabled(False)

        button_layout.addWidget(self.search_button)
        button_layout.addWidget(self.stop_button)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels(
            ["File", "Type", "Line/Row", "Match", "Full Path"]
        )
        self.results_table.setSortingEnabled(True)
        self.results_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(self.show_context_menu)
        self.results_table.cellDoubleClicked.connect(self.open_selected_file)

        layout.addLayout(folder_layout)
        layout.addLayout(file_layout)
        layout.addLayout(search_layout)
        layout.addLayout(line_layout)
        layout.addWidget(self.case_checkbox)
        layout.addLayout(button_layout)
        layout.addWidget(self.results_table)

        self.setLayout(layout)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")

        if folder:
            self.folder_path = folder
            self.folder_label.setText(folder)

    def clear_folder(self):
        self.folder_path = ""
        self.folder_label.setText("No folder selected")

    def select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File")

        if file_path:
            self.file_path = file_path
            self.file_label.setText(file_path)

    def clear_file(self):
        self.file_path = ""
        self.file_label.setText("No file selected")

    def search_files(self):
        self.results_table.setSortingEnabled(False)
        self.results_table.setRowCount(0)

        if not self.folder_path and not self.file_path:
            self.add_result(
                "ERROR",
                "",
                "",
                "Select either a folder or a specific file.",
                ""
            )
            return

        try:
            wanted_lines = parse_line_ranges(self.line_range_box.text())
        except ValueError:
            self.add_result(
                "ERROR",
                "",
                "",
                "Invalid line range. Use examples like 10, 15-20, 35.",
                ""
            )
            return

        search_term = self.search_box.text().strip()
        selected_type = self.file_type_dropdown.currentText()
        extensions = FILE_TYPES[selected_type]
        case_sensitive = self.case_checkbox.isChecked()

        self.search_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.folder_button.setEnabled(False)
        self.file_button.setEnabled(False)

        self.worker = FileSearchWorker(
            self.folder_path,
            self.file_path,
            extensions,
            search_term,
            case_sensitive,
            wanted_lines
        )

        self.worker.batch_found.connect(self.add_results_batch)
        self.worker.error_found.connect(self.show_worker_error)
        self.worker.finished_search.connect(self.search_finished)
        self.worker.start()

    def stop_search(self):
        if self.worker:
            self.worker.stop()

    def search_finished(self):
        self.results_table.setSortingEnabled(True)
        self.results_table.resizeColumnsToContents()

        self.search_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.folder_button.setEnabled(True)
        self.file_button.setEnabled(True)

        if self.results_table.rowCount() == 0:
            self.add_result("NONE", "", "", "No matching results found.", "")

        self.worker = None

    def show_worker_error(self, message):
        self.add_result("ERROR", "", "", message, "")

    def add_results_batch(self, rows):
        self.results_table.setUpdatesEnabled(False)

        for values in rows:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)

            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.results_table.setItem(row, column, item)

        self.results_table.setUpdatesEnabled(True)

    def add_result(self, filename, filetype, line_or_row, preview, full_path):
        self.add_results_batch([[filename, filetype, line_or_row, preview, full_path]])

    def get_path_from_row(self, row):
        item = self.results_table.item(row, 4)
        if item:
            return item.text()
        return ""

    def open_selected_file(self, row, column):
        file_path = self.get_path_from_row(row)

        if not file_path:
            return

        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", file_path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])
        except Exception as e:
            self.add_result("ERROR", "", "", f"Could not open file: {e}", file_path)

    def show_context_menu(self, position):
        row = self.results_table.rowAt(position.y())

        if row < 0:
            return

        file_path = self.get_path_from_row(row)

        menu = QMenu(self)

        open_action = menu.addAction("Open File")
        copy_path_action = menu.addAction("Copy Full Path")
        copy_folder_action = menu.addAction("Copy Folder Path")

        action = menu.exec(self.results_table.viewport().mapToGlobal(position))

        if action == open_action:
            self.open_selected_file(row, 0)
        elif action == copy_path_action:
            QApplication.clipboard().setText(file_path)
        elif action == copy_folder_action:
            QApplication.clipboard().setText(str(Path(file_path).parent))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ProjectFileSearchApp()
    window.show()
    sys.exit(app.exec())