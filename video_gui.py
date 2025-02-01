#!/usr/bin/env python3

import sys
import os
from pathlib import Path
from PyQt5.QtCore import (
    Qt, QSettings, QThread, pyqtSignal, pyqtSlot
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QLabel,
    QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QFileDialog, QLineEdit, QProgressBar, QTextEdit, QMessageBox
)

# ------------------ IMPORT YOUR EXISTING FUNCTIONS ------------------
# We'll assume your script is named video_processor.py
# containing copy_files, get_stream_info, ...
# If it's in the same folder, do:
from video_preprocessor import (
    # rename them if you'd like
    copy_files, 
    inspect_clips_for_mismatch,
    prompt_for_normalization,
    normalize_and_overlay,
    create_file_list,
    concatenate_videos,
    get_real_creation_time,   # only if needed
    escape_drawtext_text,     # only if needed
    SOURCE_DIR, BACKUP_DIR, COMPILE_DIR
)
# You might import other needed constants or rename them here.


# ------------------ WORKER THREAD ------------------

class VideoProcessWorker(QThread):
    """
    A background worker that runs your pipeline so the UI doesn't freeze.
    """
    progress_signal = pyqtSignal(int)       # For numeric progress
    log_signal = pyqtSignal(str)            # For log messages
    finished_signal = pyqtSignal(str)       # Emitted when pipeline is complete or errored

    def __init__(self, source_dir, backup_dir, compile_dir, selected_paths, project_name, parent=None):
        super().__init__(parent)
        self.source_dir = source_dir
        self.backup_dir = backup_dir
        self.compile_dir = compile_dir
        self.selected_paths = selected_paths
        self.project_name = project_name
        self.cancelled = False

    def run(self):
        """
        The high-level pipeline:
         1) Copy new files
         2) Inspect mismatch, maybe prompt user? (or we handle that in the UI before)
         3) Normalize & overlay each selected clip
         4) Concat
        """
        try:
            self.log("Starting pipeline...")

            # 1) Copy new files
            self.log("Copying files to backup...")
            file_data = copy_files(self.source_dir, self.backup_dir)
            if not file_data:
                self.log("No new files to process after copy.")
                self.finished_signal.emit("Done (no new files).")
                return

            # Build the final list of items to process in the next step
            # (If you want to unify your "selected_paths" with "file_data", you can do so.)
            # For demonstration, let's say "file_data" are the new ones. You might want to 
            # combine them with "selected_paths" from the UI, etc.

            all_paths = [dest for (dest, _) in file_data]

            # 2) Inspect for mismatch
            self.log("Inspecting for mismatches...")
            unique_specs, file_specs = inspect_clips_for_mismatch(all_paths)

            target_spec = None
            if len(unique_specs) > 1:
                # In a real GUI, you'd show a dialog. 
                # For now, let's just call prompt_for_normalization() in the console. 
                self.log("Multiple specs found. Prompting in console (blocks GUI).")
                chosen = prompt_for_normalization(unique_specs)
                if chosen:
                    target_spec = chosen

            # 3) Preprocess in a loop, updating progress
            self.log("Preprocessing clips with normalize_and_overlay...")
            total = len(file_data)
            success_count = 0

            for i, item in enumerate(file_data):
                if self.cancelled:
                    self.log("Processing canceled by user.")
                    self.finished_signal.emit("Canceled")
                    return

                # item is (dest_path, creation_dt)
                self.log(f"Processing {item[0]}...")
                result = normalize_and_overlay(item, tmp_dir=os.path.join(self.backup_dir, self.project_name, "tmp"), target_specs=target_spec)

                if result:
                    success_count += 1
                prog = int(((i+1) / total) * 100)
                self.progress_signal.emit(prog)

            if success_count == 0:
                self.log("No files successfully preprocessed.")
                self.finished_signal.emit("Done (no preprocessed).")
                return

            # 4) Create file list & concat
            tmp_dir = os.path.join(self.backup_dir, self.project_name, "tmp")
            file_list = create_file_list(tmp_dir, file_data)

            out_file = os.path.join(self.compile_dir, f"{self.project_name}.mp4")
            self.log(f"Concatenating final video -> {out_file}")
            concatenate_videos(file_list, out_file)

            self.log("Pipeline complete!")
            self.finished_signal.emit("Done (pipeline complete).")

        except Exception as e:
            self.log(f"Error in processing: {e}")
            self.finished_signal.emit("Error occurred")

    def log(self, message: str):
        self.log_signal.emit(message)

    def cancel(self):
        self.cancelled = True


# ------------------ MAIN WINDOW (GUI) ------------------

from PyQt5.QtWidgets import QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QProgressBar, QTextEdit

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video GUI")
        self.resize(1200, 700)

        # QSettings to remember last used directories
        from PyQt5.QtCore import QSettings
        self.settings = QSettings("MyCompany", "VideoProcessorGUI")

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # Row 1: Directories
        dir_layout = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.backup_edit = QLineEdit()
        self.compile_edit = QLineEdit()

        # Load defaults from your python script or from QSettings
        self.source_edit.setText(self.settings.value("source_dir", SOURCE_DIR))
        self.backup_edit.setText(self.settings.value("backup_dir", BACKUP_DIR))
        self.compile_edit.setText(self.settings.value("compile_dir", COMPILE_DIR))

        src_btn = QPushButton("Browse Source")
        src_btn.clicked.connect(self.browse_source)
        dir_layout.addWidget(QLabel("Source:"))
        dir_layout.addWidget(self.source_edit)
        dir_layout.addWidget(src_btn)

        bkp_btn = QPushButton("Browse Backup")
        bkp_btn.clicked.connect(self.browse_backup)
        dir_layout.addWidget(QLabel("Backup:"))
        dir_layout.addWidget(self.backup_edit)
        dir_layout.addWidget(bkp_btn)

        cmp_btn = QPushButton("Browse Final")
        cmp_btn.clicked.connect(self.browse_compile)
        dir_layout.addWidget(QLabel("Compilation:"))
        dir_layout.addWidget(self.compile_edit)
        dir_layout.addWidget(cmp_btn)

        main_layout.addLayout(dir_layout)

        # Row 2: Table of .mp4
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Select", "Filename"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        main_layout.addWidget(self.table)

        # Row 3: Controls
        ctl_layout = QHBoxLayout()
        self.scan_button = QPushButton("Scan Folder")
        self.scan_button.clicked.connect(self.scan_folder)
        ctl_layout.addWidget(self.scan_button)

        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Enter project name (e.g. December 2025)")
        ctl_layout.addWidget(self.project_edit)

        self.start_button = QPushButton("Start Processing")
        self.start_button.clicked.connect(self.start_processing)
        ctl_layout.addWidget(self.start_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.cancel_button.setEnabled(False)
        ctl_layout.addWidget(self.cancel_button)

        main_layout.addLayout(ctl_layout)

        # Row 4: Progress + Log
        self.progress_bar = QProgressBar()
        main_layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        main_layout.addWidget(self.log_text)

        self.worker = None  # We'll create one when user hits start

    def browse_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if folder:
            self.source_edit.setText(folder)
            self.settings.setValue("source_dir", folder)

    def browse_backup(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Backup Folder")
        if folder:
            self.backup_edit.setText(folder)
            self.settings.setValue("backup_dir", folder)

    def browse_compile(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Compilation Folder")
        if folder:
            self.compile_edit.setText(folder)
            self.settings.setValue("compile_dir", folder)

    def scan_folder(self):
        """Scan the source folder, list .mp4 files in the table."""
        src_dir = self.source_edit.text().strip()
        if not os.path.isdir(src_dir):
            QMessageBox.warning(self, "Invalid Source", "Please pick a valid source directory.")
            return
        mp4_files = sorted(Path(src_dir).rglob("*.mp4"))

        self.table.setRowCount(0)
        self.table.setRowCount(len(mp4_files))

        for row, p in enumerate(mp4_files):
            check_item = QTableWidgetItem()
            check_item.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, check_item)

            file_item = QTableWidgetItem(str(p.relative_to(src_dir)))
            file_item.setFlags(file_item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(row, 1, file_item)

        self.log_text.append(f"Found {len(mp4_files)} mp4 files.")

    def start_processing(self):
        """Gather user inputs, start the pipeline in a background thread."""
        src_dir = self.source_edit.text().strip()
        bkp_dir = self.backup_edit.text().strip()
        cmp_dir = self.compile_edit.text().strip()
        project_name = self.project_edit.text().strip()

        if not project_name:
            QMessageBox.warning(self, "No Project Name", "Please enter a project name.")
            return

        if not (os.path.isdir(src_dir) and os.path.isdir(bkp_dir) and os.path.isdir(cmp_dir)):
            QMessageBox.warning(self, "Invalid Folders", "Please check source, backup, and compilation folders.")
            return

        # Collect selected items
        selected_paths = []
        row_count = self.table.rowCount()
        for row in range(row_count):
            check_item = self.table.item(row, 0)
            if check_item and check_item.checkState() == Qt.Checked:
                file_item = self.table.item(row, 1)
                if file_item:
                    relative_path = file_item.text()
                    full_path = os.path.join(src_dir, relative_path)
                    selected_paths.append(full_path)

        if not selected_paths:
            QMessageBox.information(self, "Nothing Selected", "Please select at least one .mp4 file.")
            return

        # Create and start our worker
        self.worker = VideoProcessWorker(
            source_dir=src_dir,
            backup_dir=bkp_dir,
            compile_dir=cmp_dir,
            selected_paths=selected_paths,
            project_name=project_name
        )
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.log_signal.connect(self.on_log)
        self.worker.finished_signal.connect(self.on_finished)

        self.log_text.append("Starting processing thread...")
        self.worker.start()
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

    def cancel_processing(self):
        if self.worker:
            self.worker.cancel()
            self.log_text.append("Cancellation requested...")

    @pyqtSlot(int)
    def on_progress(self, val):
        self.progress_bar.setValue(val)

    @pyqtSlot(str)
    def on_log(self, msg):
        self.log_text.append(msg)

    @pyqtSlot(str)
    def on_finished(self, status):
        self.log_text.append(f"Worker finished: {status}")
        self.progress_bar.setValue(0)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.worker = None


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
