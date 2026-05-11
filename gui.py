import os
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QTextEdit,
)

from dmm_comm import (
    CURRENT_RANGES,
    DmmProbeResult,
    NPLC_OPTIONS,
    VOLTAGE_RANGES,
    list_serial_ports,
    make_csv_filename,
    probe_dmm_port,
)
from logger_worker import DmmConfig, DmmLoggerWorker, Tc08Config


MEASUREMENT_OPTIONS = [
    ("DC Voltage", "voltage"),
    ("DC Current", "current"),
]

TC08_TYPES = [
    ("K", "K"),
    ("J", "J"),
    ("T", "T"),
    ("E", "E"),
    ("N", "N"),
    ("R", "R"),
    ("S", "S"),
    ("B", "B"),
]


class DmmLoggerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DMM Logger")
        self.worker_thread: QThread | None = None
        self.worker = None
        self.dmm_panels: list[dict] = []
        self.detected_dmms: list[DmmProbeResult] = []
        self.init_ui()

        self.recording_flash_timer = QTimer(self)
        self.recording_flash_timer.setSingleShot(True)
        self.recording_flash_timer.timeout.connect(self.reset_recording_indicator)

    def init_ui(self) -> None:
        central = QWidget()
        main_layout = QVBoxLayout(central)

        controls_layout = QHBoxLayout()
        self.dmm_count_combo = QComboBox()
        self.dmm_count_combo.addItems(["1", "2", "3", "4"])
        self.dmm_count_combo.currentIndexChanged.connect(self.on_dmm_count_changed)
        controls_layout.addWidget(QLabel("DMMs:"))
        controls_layout.addWidget(self.dmm_count_combo)

        self.detect_button = QPushButton("Detect DMMs")
        self.detect_button.clicked.connect(self.on_detect_dmm_clicked)
        controls_layout.addWidget(self.detect_button)

        self.start_button = QPushButton("Start Logging")
        self.start_button.clicked.connect(self.on_start_logging)
        controls_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Logging")
        self.stop_button.clicked.connect(self.on_stop_logging)
        self.stop_button.setEnabled(False)
        controls_layout.addWidget(self.stop_button)

        self.recording_indicator = QLabel("REC")
        self.recording_indicator.setAlignment(Qt.AlignCenter)
        self.recording_indicator.setFixedWidth(60)
        self.recording_indicator.setStyleSheet(
            "background-color: lightgray; color: black; border: 1px solid gray; border-radius: 8px;"
        )
        controls_layout.addWidget(self.recording_indicator)

        controls_layout.addWidget(QLabel("Sample Period (s):"))
        self.sample_period_edit = QLineEdit("1.0")
        controls_layout.addWidget(self.sample_period_edit)

        main_layout.addLayout(controls_layout)

        self.tab_widget = QTabWidget()

        dmm_tab = QWidget()
        dmm_tab_layout = QVBoxLayout(dmm_tab)
        panel_grid = QGridLayout()
        self.dmm_panels = [self.create_dmm_panel(i + 1) for i in range(4)]
        for index, panel in enumerate(self.dmm_panels):
            row = index // 2
            col = index % 2
            panel_grid.addWidget(panel["group"], row, col)
        dmm_tab_layout.addLayout(panel_grid)
        self.tab_widget.addTab(dmm_tab, "DMMs")

        temp_tab = QWidget()
        temp_tab_layout = QVBoxLayout(temp_tab)
        self.tc08_tab = self.create_tc08_tab()
        self.refresh_tc08_buttons()
        self.update_tc08_summary()
        temp_tab_layout.addWidget(self.tc08_tab["widget"])
        self.tab_widget.addTab(temp_tab, "Temp")

        main_layout.addWidget(self.tab_widget)

        self.status_area = QTextEdit()
        self.status_area.setReadOnly(True)
        self.status_area.setMinimumHeight(180)
        main_layout.addWidget(self.status_area)

        self.setCentralWidget(central)
        self.on_dmm_count_changed(0)

    def create_dmm_panel(self, index: int) -> dict:
        group = QGroupBox(f"DMM {index}")
        layout = QVBoxLayout(group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("COM port:"))
        port_combo = QComboBox()
        row1.addWidget(port_combo)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Measurement:"))
        measurement_combo = QComboBox()
        for label, value in MEASUREMENT_OPTIONS:
            measurement_combo.addItem(label, value)
        measurement_combo.currentIndexChanged.connect(lambda _: self.on_measurement_changed(index - 1))
        row2.addWidget(measurement_combo)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Range:"))
        range_combo = QComboBox()
        row3.addWidget(range_combo)
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("NPLC:"))
        nplc_combo = QComboBox()
        for value in NPLC_OPTIONS:
            nplc_combo.addItem(value)
        nplc_combo.setCurrentText("1")
        row4.addWidget(nplc_combo)
        layout.addLayout(row4)

        row5 = QHBoxLayout()
        row5.addWidget(QLabel("Label:"))
        label_edit = QLineEdit(f"DMM {index}")
        row5.addWidget(label_edit)
        layout.addLayout(row5)

        row6 = QHBoxLayout()
        row6.addWidget(QLabel("Latest reading:"))
        reading_label = QLabel("N/A")
        reading_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row6.addWidget(reading_label)
        layout.addLayout(row6)

        panel = {
            "group": group,
            "port": port_combo,
            "measurement": measurement_combo,
            "range": range_combo,
            "nplc": nplc_combo,
            "label": label_edit,
            "reading": reading_label,
        }
        self.populate_range_items(panel)
        return panel

    def populate_range_items(self, panel: dict) -> None:
        panel["range"].clear()
        measurement_type = panel["measurement"].currentData()
        options = VOLTAGE_RANGES if measurement_type == "voltage" else CURRENT_RANGES
        
        for value in options:
            display_text = self.format_range_display(value, measurement_type)
            panel["range"].addItem(display_text, value)  # display_text, userData=value
        panel["range"].setCurrentIndex(0)

    def create_tc08_tab(self) -> dict:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.tc08_enable_checkbox = QCheckBox("Enable temperature logging")
        self.tc08_enable_checkbox.setChecked(False)
        self.tc08_enable_checkbox.stateChanged.connect(self.on_tc08_master_enabled_changed)
        layout.addWidget(self.tc08_enable_checkbox)

        channel_grid = QGridLayout()
        channel_buttons: list[QPushButton] = []
        channel_configs: list[dict] = []
        for channel in range(1, 9):
            button = QPushButton(str(channel))
            button.setFixedSize(60, 60)
            button.clicked.connect(lambda _, idx=channel - 1: self.open_tc08_channel_dialog(idx))
            channel_grid.addWidget(button, (channel - 1) // 4, (channel - 1) % 4)
            channel_buttons.append(button)
            channel_configs.append(
                {
                    "channel": channel,
                    "enabled": False,
                    "tc_type": "K",
                    "label": f"TC08 CH{channel}",
                    "last_temp": None,
                }
            )

        layout.addLayout(channel_grid)

        summary_label = QLabel("No TC-08 channels configured.")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        return {
            "widget": widget,
            "buttons": channel_buttons,
            "configs": channel_configs,
            "summary": summary_label,
            "enable_checkbox": self.tc08_enable_checkbox,
        }

    def open_tc08_channel_dialog(self, index: int) -> None:
        config = self.tc08_tab["configs"][index]
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Configure TC-08 channel {config['channel']}")

        dialog_layout = QVBoxLayout(dialog)

        enabled_checkbox = QCheckBox("Enable this channel")
        enabled_checkbox.setChecked(config["enabled"])
        dialog_layout.addWidget(enabled_checkbox)

        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Label:"))
        label_edit = QLineEdit(config["label"])
        label_row.addWidget(label_edit)
        dialog_layout.addLayout(label_row)

        tc_type_row = QHBoxLayout()
        tc_type_row.addWidget(QLabel("Thermocouple type:"))
        tc_type_combo = QComboBox()
        for label, value in TC08_TYPES:
            tc_type_combo.addItem(label, value)
        tc_type_combo.setCurrentText(config["tc_type"])
        tc_type_row.addWidget(tc_type_combo)
        dialog_layout.addLayout(tc_type_row)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        dialog_layout.addWidget(button_box)

        if dialog.exec() != QDialog.Accepted:
            return

        config["enabled"] = enabled_checkbox.isChecked()
        config["label"] = label_edit.text().strip() or f"TC08 CH{config['channel']}"
        config["tc_type"] = tc_type_combo.currentData()
        if not config["enabled"]:
            config["last_temp"] = None

        self.refresh_tc08_buttons()
        self.update_tc08_summary()

    def refresh_tc08_buttons(self) -> None:
        for index, button in enumerate(self.tc08_tab["buttons"]):
            config = self.tc08_tab["configs"][index]
            style = ""
            if config["enabled"]:
                style = (
                    "background-color: #1a4b8d; color: white; border: 1px solid #183f73;"
                )
                button.setText(f"{config['channel']}\n{config['tc_type']}")
                button.setToolTip(f"{config['label']} ({config['tc_type']})")
            else:
                style = (
                    "background-color: #f0f0f0; color: black; border: 1px solid #999999;"
                )
                button.setText(str(config["channel"]))
                button.setToolTip("Click to configure")
            button.setStyleSheet(style)

        self.on_tc08_master_enabled_changed()

    def update_tc08_summary(self) -> None:
        enabled_channels = [cfg for cfg in self.tc08_tab["configs"] if cfg["enabled"]]
        if not enabled_channels:
            self.tc08_tab["summary"].setText("No TC-08 channels configured.")
            return

        summary_lines = ["Enabled TC-08 channels:"]
        for cfg in enabled_channels:
            summary_lines.append(
                f"{cfg['channel']}: {cfg['label']} ({cfg['tc_type']})"
            )

        if not self.tc08_tab["enable_checkbox"].isChecked():
            summary_lines.append("(Temperature logging is currently disabled.)")

        self.tc08_tab["summary"].setText("\n".join(summary_lines))

    def on_tc08_master_enabled_changed(self) -> None:
        self.update_tc08_summary()

    def format_range_display(self, value: str, measurement_type: str) -> str:
        """Format range value for display with appropriate units."""
        try:
            num_value = float(value)
        except ValueError:
            return value
            
        if measurement_type == "voltage":
            if num_value < 1:
                return f"{int(num_value * 1000)} mV"
            else:
                return f"{int(num_value)} V"
        elif measurement_type == "current":
            if num_value < 0.001:
                return f"{int(num_value * 1000000)} µA"
            elif num_value < 1:
                return f"{int(num_value * 1000)} mA"
            else:
                return f"{int(num_value)} A"
        return value

    def format_reading_display(self, value: float, measurement_type: str, measurement_range: str | None) -> str:
        """Format a numeric DMM reading using the selected range units."""
        range_value: float | None = None
        if measurement_range is not None:
            try:
                range_value = float(measurement_range)
            except ValueError:
                range_value = None

        if measurement_type == "voltage":
            if range_value is not None and range_value < 1:
                return f"{value * 1000:.6g} mV"
            return f"{value:.6g} V"
        elif measurement_type == "current":
            if range_value is not None and range_value < 0.001:
                return f"{value * 1_000_000:.6g} µA"
            if range_value is not None and range_value < 1:
                return f"{value * 1000:.6g} mA"
            return f"{value:.6g} A"
        if measurement_type == "temperature":
            return f"{value:.3f} °C"
        return f"{value:.6g}"

    def set_recording_indicator(self, active: bool) -> None:
        if active:
            self.recording_indicator.setStyleSheet(
                "background-color: #33aa33; color: white; border: 1px solid #227722; border-radius: 8px;"
            )
        else:
            self.recording_indicator.setStyleSheet(
                "background-color: lightgray; color: black; border: 1px solid gray; border-radius: 8px;"
            )

    def reset_recording_indicator(self) -> None:
        self.set_recording_indicator(True)

    def flash_recording_indicator(self) -> None:
        self.recording_indicator.setStyleSheet(
            "background-color: yellow; color: black; border: 1px solid #bbbb22; border-radius: 8px;"
        )
        self.recording_flash_timer.start(180)

    def on_measurement_changed(self, index: int) -> None:
        self.populate_range_items(self.dmm_panels[index])

    def on_dmm_count_changed(self, index: int) -> None:
        visible = index + 1
        for i, panel in enumerate(self.dmm_panels):
            panel["group"].setVisible(i < visible)

    def populate_dmm_port_items(self) -> None:
        for panel in self.dmm_panels:
            panel["port"].clear()
            panel["port"].addItem("Select detected DMM", None)
            for result in self.detected_dmms:
                display = f"{result.port} - {result.idn}"
                panel["port"].addItem(display, (result.port, result.idn))

    @Slot()
    def on_detect_dmm_clicked(self) -> None:
        ports = list_serial_ports()
        if not ports:
            QMessageBox.warning(self, "Detect DMMs", "No serial ports were found.")
            self.detected_dmms = []
            self.populate_dmm_port_items()
            return

        self.detected_dmms = []
        for port in ports:
            self.append_status(f"Probing {port}...")
            idn = probe_dmm_port(port, timeout_s=1.0)
            if idn:
                self.detected_dmms.append(DmmProbeResult(port=port, idn=idn))
                self.append_status(f"Found instrument on {port}: {idn}")
            else:
                self.append_status(f"No instrument response on {port}")

        self.populate_dmm_port_items()
        if not self.detected_dmms:
            QMessageBox.information(self, "Detect DMMs", "No SCPI-compatible DMMs were detected.")
            return

        self.append_status(f"Detected {len(self.detected_dmms)} DMM(s)")
        active_count = int(self.dmm_count_combo.currentText())
        for index in range(active_count):
            if index < len(self.detected_dmms):
                self.dmm_panels[index]["port"].setCurrentIndex(index + 1)

    @Slot()
    def on_start_logging(self) -> None:
        if self.worker_thread is not None:
            return

        configs = []
        tc08_configs = []
        active_count = int(self.dmm_count_combo.currentText())
        for index in range(active_count):
            panel = self.dmm_panels[index]
            selected_device = panel["port"].currentData()
            if not selected_device or not isinstance(selected_device, tuple):
                QMessageBox.warning(self, "Start Logging", f"Please select a detected DMM for DMM {index + 1}.")
                return

            port, idn = selected_device
            measurement_type = panel["measurement"].currentData()
            measurement_range = panel["range"].currentData()
            nplc = panel["nplc"].currentText().strip()

            configs.append(
                DmmConfig(
                    label=panel["label"].text().strip() or f"DMM {index + 1}",
                    port=port,
                    measurement_type=measurement_type,
                    measurement_range=measurement_range,
                    nplc=nplc,
                    idn=idn,
                )
            )

        if self.tc08_tab["enable_checkbox"].isChecked():
            for channel_index, channel_config in enumerate(self.tc08_tab["configs"]):
                if channel_config["enabled"]:
                    tc08_configs.append(
                        Tc08Config(
                            label=channel_config["label"],
                            channel=channel_config["channel"],
                            tc_type=channel_config["tc_type"],
                        )
                    )

        try:
            sample_period = float(self.sample_period_edit.text().strip())
            if sample_period <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Start Logging", "Please enter a valid positive number for sample period.")
            return

        default_name = make_csv_filename(datetime.now())
        csv_path, _ = QFileDialog.getSaveFileName(self, "Save CSV log", default_name, "CSV files (*.csv)")
        if not csv_path:
            return
        if not csv_path.lower().endswith(".csv"):
            csv_path += ".csv"

        self.worker = DmmLoggerWorker(
            configs=configs,
            tc08_configs=tc08_configs,
            csv_path=csv_path,
            sample_period_s=sample_period,
        )

        self.worker_thread = QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker_thread.finished.connect(self.on_worker_finished)
        self.worker.status_updated.connect(self.append_status)
        self.worker.reading_updated.connect(self.on_reading_updated)
        self.worker.tc08_reading_updated.connect(self.on_tc08_reading_updated)
        self.worker.error_occurred.connect(self.on_worker_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)

        self.worker_thread.start()
        self.set_recording_indicator(True)
        self.start_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.dmm_count_combo.setEnabled(False)
        self.append_status(f"Started logging to {csv_path}")

    @Slot()
    def on_stop_logging(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.set_recording_indicator(False)

    @Slot(int, float)
    def on_reading_updated(self, index: int, value: float) -> None:
        if index < len(self.dmm_panels):
            panel = self.dmm_panels[index]
            measurement_type = panel["measurement"].currentData()
            measurement_range = panel["range"].currentData()
            panel["reading"].setText(
                self.format_reading_display(value, measurement_type, measurement_range)
            )
            self.flash_recording_indicator()

    @Slot(int, float)
    def on_tc08_reading_updated(self, index: int, value: float) -> None:
        if 0 <= index < len(self.tc08_tab["buttons"]):
            button = self.tc08_tab["buttons"][index]
            channel_config = self.tc08_tab["configs"][index]
            button.setToolTip(f"{channel_config['label']}: {value:.3f} °C")
        self.flash_recording_indicator()

    @Slot(str)
    def on_worker_error(self, message: str) -> None:
        self.append_status(f"Error: {message}")
        QMessageBox.critical(self, "Logging failed", message)

    @Slot()
    def on_worker_finished(self) -> None:
        self.worker_thread = None
        self.worker = None
        self.start_button.setEnabled(True)
        self.detect_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.dmm_count_combo.setEnabled(True)
        self.append_status("Logging stopped.")

    def on_tc08_enabled_changed(self) -> None:
        enabled = self.tc08_panel["enabled"].isChecked()
        self.tc08_panel["channel"].setEnabled(enabled)
        self.tc08_panel["tc_type"].setEnabled(enabled)
        self.tc08_panel["label"].setEnabled(enabled)
        if not enabled:
            self.tc08_panel["reading"].setText("N/A")

    def append_status(self, message: str) -> None:
        self.status_area.append(message)
        self.status_area.verticalScrollBar().setValue(self.status_area.verticalScrollBar().maximum())


def run_app() -> None:
    app = QApplication([])
    window = DmmLoggerWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    run_app()
