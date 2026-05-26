"""Main Qt GUI for configuring instruments, logging runs, and live plots."""

import os
import math
from datetime import datetime

from qt_compat import *

import pyqtgraph as pg

pg.setConfigOptions(antialias=True)

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
from logger_worker import (
    ChamberConfig,
    ChamberProfilePoint,
    chamber_profile_duration_s,
    intended_chamber_temperature,
)
from thermo_comm import detect_chamber_ports


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

TC08_DISABLED_COLOR = "#8fcaf0"
TC08_DEFAULT_ENABLED_COLOR = "#1a4b8d"
CHAMBER_MIN_TEMP_C = -70.0
CHAMBER_MAX_TEMP_C = 130.0
CHAMBER_ACTUAL_COLOR = "#b3261e"
CHAMBER_INTENDED_COLOR = "#2557a7"


class CircularBuffer:
    """A fixed-size circular buffer for storing time-series data."""
    
    def __init__(self, max_size=100):
        self.max_size = max_size
        self.data = []
        self.timestamps = []
        self.write_pos = 0
        
    def append(self, value, timestamp):
        """Add a value to the buffer."""
        if len(self.data) < self.max_size:
            self.data.append(value)
            self.timestamps.append(timestamp)
        else:
            self.data[self.write_pos] = value
            self.timestamps[self.write_pos] = timestamp
            self.write_pos = (self.write_pos + 1) % self.max_size
    
    def get_data(self):
        """Return (timestamps, values) in chronological order."""
        if not self.data:
            return [], []
        
        if len(self.data) < self.max_size:
            return self.timestamps, self.data
        
        # Reorder to chronological order (write_pos is the oldest)
        timestamps = self.timestamps[self.write_pos:] + self.timestamps[:self.write_pos]
        data = self.data[self.write_pos:] + self.data[:self.write_pos]
        return timestamps, data
    
    def clear(self):
        """Clear the buffer."""
        self.data = []
        self.timestamps = []
        self.write_pos = 0
    
    def set_max_size(self, new_size):
        """Resize the buffer."""
        self.max_size = new_size
        if len(self.data) > new_size:
            self.data = self.data[-new_size:]
            self.timestamps = self.timestamps[-new_size:]
        self.write_pos = 0


class RoundedChannelButton(QGraphicsItem):
    """A graphics item that displays a rounded rectangle with a channel number."""
    
    def __init__(self, channel, x, y, width, height, color=TC08_DISABLED_COLOR, parent=None):
        super().__init__(parent)
        self.channel = channel
        self.rect = QRectF(x, y, width, height)
        self.color = color  # Store the channel color
        self.brush = QBrush(QColor(color))
        self.corner_radius = 4
        self.setAcceptHoverEvents(True)
        
    def boundingRect(self):
        return self.rect
    
    def paint(self, painter, option, widget=None):
        # Create rounded rectangle path
        path = QPainterPath()
        path.addRoundedRect(self.rect, self.corner_radius, self.corner_radius)
        
        # Draw the rounded rectangle
        painter.fillPath(path, self.brush)
        
        # Draw text
        painter.setPen(Qt.black)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(self.rect.toRect(), Qt.AlignCenter, str(self.channel))
    
    def set_color(self, color_hex):
        """Update the button color."""
        self.color = color_hex
        self.brush = QBrush(QColor(color_hex))
        self.update()


class TC08GraphicsView(QGraphicsView):
    """Custom graphics view for TC-08 device image with clickable channels."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.channel_items = {}  # Map channel -> RoundedChannelButton
        self.on_channel_clicked = None
        self.setStyleSheet("border: none; background-color: white;")
        
    def setup_image(self, image_path, parent_window):
        """Load image and create clickable channel regions."""
        scene = QGraphicsScene()
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            print(f"Failed to load image: {image_path}")
            return False
        
        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        scene.addItem(self.pixmap_item)
        self.setScene(scene)
        
        # Define channel click regions (x, y, width, height)
        # Calibrated from actual TC-08 image (141x242)
        original_rects = {
            1: QRect(31, 203, 45, 30),
            2: QRect(25, 175, 45, 30),
            3: QRect(25, 148, 45, 30),
            4: QRect(31, 120, 45, 30),
            5: QRect(67, 120, 45, 30),
            6: QRect(73, 148, 45, 30),
            7: QRect(73, 177, 45, 30),
            8: QRect(67, 204, 45, 30),
        }
        
        # Reduce size: 1/4 smaller in width, 1/6 smaller in height
        # New width: 45 * 0.75 = 33.75 ≈ 34, new height: 30 * (5/6) ≈ 25
        for channel, rect in original_rects.items():
            new_width = int(rect.width() * 0.75)  # 1/4 smaller
            new_height = int(rect.height() * 5 / 6)  # 1/6 smaller
            dx = (rect.width() - new_width) // 2
            dy = (rect.height() - new_height) // 2
            
            button = RoundedChannelButton(
                channel,
                rect.x() + dx,
                rect.y() + dy,
                new_width,
                new_height,
                color=TC08_DISABLED_COLOR
            )
            scene.addItem(button)
            self.channel_items[channel] = button
        
        self.setSceneRect(scene.itemsBoundingRect())
        return True
    
    def update_channel_state(self, channel_colors):
        """Update button colors based on channel configuration.
        
        Args:
            channel_colors: dict mapping channel number -> color hex string
        """
        for channel, button in self.channel_items.items():
            if channel in channel_colors:
                button.set_color(channel_colors[channel])
    
    def mousePressEvent(self, event):
        """Handle mouse clicks on channels."""
        scene_pos = self.mapToScene(event.pos())
        
        for channel, button in self.channel_items.items():
            if button.rect.contains(scene_pos):
                if self.on_channel_clicked:
                    self.on_channel_clicked(channel - 1)  # Convert to 0-based index
                break


class DmmLoggerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ThermoPi")
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
        dmm_controls_layout = QHBoxLayout()
        self.dmm_count_combo = QComboBox()
        self.dmm_count_combo.addItems(["0", "1", "2", "3", "4"])
        self.dmm_count_combo.setCurrentText("0")
        self.dmm_count_combo.currentIndexChanged.connect(self.on_dmm_count_changed)
        dmm_controls_layout.addWidget(QLabel("DMMs:"))
        dmm_controls_layout.addWidget(self.dmm_count_combo)
        dmm_controls_layout.addStretch()
        dmm_tab_layout.addLayout(dmm_controls_layout)

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

        chamber_tab = QWidget()
        chamber_tab_layout = QVBoxLayout(chamber_tab)
        self.chamber_tab = self.create_chamber_tab()
        chamber_tab_layout.addWidget(self.chamber_tab["widget"])
        self.tab_widget.addTab(chamber_tab, "Thermal Chamber")

        plot_tab = QWidget()
        plot_tab_layout = QVBoxLayout(plot_tab)
        self.plot_tab = self.create_plot_tab()
        self.plot_tab["sample_spinbox"].valueChanged.connect(self.on_plot_sample_count_changed)
        plot_tab_layout.addWidget(self.plot_tab["widget"])
        self.tab_widget.addTab(plot_tab, "Plot")

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

        # Create graphics view for TC-08 image
        graphics_view = TC08GraphicsView()
        graphics_view.setup_image("TC-08img.png", self)
        graphics_view.on_channel_clicked = self.open_tc08_channel_dialog
        graphics_view.setFixedHeight(350)
        layout.addWidget(graphics_view)

        channel_configs: list[dict] = []
        for channel in range(1, 9):
            channel_configs.append(
                {
                    "channel": channel,
                    "enabled": False,
                    "tc_type": "K",
                    "label": f"TC08 CH{channel}",
                    "color": TC08_DEFAULT_ENABLED_COLOR,
                    "last_temp": None,
                }
            )

        summary_label = QLabel("No TC-08 channels configured.")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        return {
            "widget": widget,
            "graphics_view": graphics_view,
            "configs": channel_configs,
            "summary": summary_label,
        }

    def create_chamber_tab(self) -> dict:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        enable_checkbox = QCheckBox("Use thermal chamber temperature program")
        enable_checkbox.stateChanged.connect(self.on_chamber_enabled_changed)
        layout.addWidget(enable_checkbox)

        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("COM port:"))
        port_combo = QComboBox()
        port_layout.addWidget(port_combo)
        refresh_button = QPushButton("Detect Chamber")
        refresh_button.clicked.connect(self.populate_chamber_port_items)
        port_layout.addWidget(refresh_button)
        port_layout.addStretch()
        layout.addLayout(port_layout)

        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Temperature C", "Hold min"])
        table.itemChanged.connect(self.update_chamber_preview)
        layout.addWidget(table)

        point_layout = QHBoxLayout()
        add_button = QPushButton("Add Temp")
        add_button.clicked.connect(self.add_chamber_temperature_step)
        point_layout.addWidget(add_button)
        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self.remove_selected_chamber_profile_points)
        point_layout.addWidget(remove_button)
        point_layout.addStretch()
        layout.addLayout(point_layout)

        preview_plot = pg.PlotWidget(title="Intended Chamber Temperature Program")
        preview_plot.setBackground("w")
        preview_plot.setLabel("left", "Temperature", units="C")
        preview_plot.setLabel("bottom", "Program time", units="min")
        preview_plot.showGrid(x=True, y=True, alpha=0.18)
        layout.addWidget(preview_plot)

        summary = QLabel("Thermal chamber program disabled.")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        tab = {
            "widget": widget,
            "enabled": enable_checkbox,
            "port": port_combo,
            "refresh": refresh_button,
            "table": table,
            "add": add_button,
            "remove": remove_button,
            "preview_plot": preview_plot,
            "preview_item": None,
            "summary": summary,
        }
        self.chamber_tab = tab
        self.populate_chamber_port_items()
        self.add_chamber_temperature_step(20.0, 10.0)
        self.on_chamber_enabled_changed()
        return tab

    def create_plot_tab(self) -> dict:
        """Create a tab with PyQtGraph plots for temperature and DMM data."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Control row
        control_layout = QHBoxLayout()
        control_layout.addWidget(QLabel("Plot samples:"))
        sample_spinbox = QSpinBox()
        sample_spinbox.setMinimum(10)
        sample_spinbox.setMaximum(1000)
        sample_spinbox.setValue(10)
        sample_spinbox.setSingleStep(10)
        control_layout.addWidget(sample_spinbox)
        control_layout.addStretch()
        layout.addLayout(control_layout)

        # Create separate plot widgets for temperature, DMM, and chamber data.
        plot_container = QGridLayout()
        
        # Temperature plot
        tc_plot = pg.PlotWidget(
            title="Temperature Sensors",
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")},
        )
        tc_plot.setBackground("w")
        tc_plot.setLabel("left", "Temperature", units="°C")
        tc_plot.setLabel("bottom", "Time")
        tc_plot.addLegend()
        tc_plot.showGrid(x=True, y=True, alpha=0.18)
        plot_container.addWidget(tc_plot, 0, 0)
        
        # DMM plot
        dmm_plot = pg.PlotWidget(
            title="DMM Measurements",
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")},
        )
        dmm_plot.setBackground("w")
        dmm_plot.setLabel("left", "Measurement")
        dmm_plot.setLabel("bottom", "Time")
        dmm_plot.addLegend()
        dmm_plot.showGrid(x=True, y=True, alpha=0.18)
        plot_container.addWidget(dmm_plot, 0, 1)

        chamber_plot = pg.PlotWidget(
            title="Thermal Chamber",
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")},
        )
        chamber_plot.setBackground("w")
        chamber_plot.setLabel("left", "Temperature", units="C")
        chamber_plot.setLabel("bottom", "Time")
        chamber_plot.addLegend()
        chamber_plot.showGrid(x=True, y=True, alpha=0.18)
        plot_container.addWidget(chamber_plot, 1, 0, 1, 2)
        
        layout.addLayout(plot_container)
        
        return {
            "widget": widget,
            "tc_plot": tc_plot,
            "dmm_plot": dmm_plot,
            "sample_spinbox": sample_spinbox,
            "tc_buffers": {},  # {channel: CircularBuffer}
            "dmm_buffers": {},  # {dmm_index: CircularBuffer}
            "chamber_actual_buffer": CircularBuffer(sample_spinbox.value()),
            "chamber_intended_buffer": CircularBuffer(sample_spinbox.value()),
            "tc_plot_items": {},  # {channel: PlotDataItem}
            "dmm_plot_items": {},  # {dmm_index: PlotDataItem}
            "chamber_plot": chamber_plot,
            "chamber_actual_item": None,
            "chamber_intended_item": None,
        }

    def populate_chamber_port_items(self) -> None:
        if not hasattr(self, "chamber_tab"):
            return

        current = self.chamber_tab["port"].currentData()
        self.chamber_tab["port"].clear()
        detected_chambers = detect_chamber_ports(timeout_s=1.0)
        for chamber in detected_chambers:
            display = f"{chamber.port} - {chamber.response}"
            self.chamber_tab["port"].addItem(display, chamber.port)

        if not detected_chambers:
            self.chamber_tab["port"].addItem("No thermal chamber detected", None)
            if hasattr(self, "status_area"):
                self.append_status("No thermal chamber detected.")
            return

        if hasattr(self, "status_area"):
            self.append_status(f"Detected {len(detected_chambers)} thermal chamber port(s).")

        if current:
            index = self.chamber_tab["port"].findData(current)
            if index >= 0:
                self.chamber_tab["port"].setCurrentIndex(index)

    def add_chamber_temperature_step(self, temperature_c: float | bool = 20.0, hold_min: float = 10.0) -> None:
        if isinstance(temperature_c, bool):
            rows = self.chamber_tab["table"].rowCount()
            temperature_c = 20.0
            if rows > 0:
                last_item = self.chamber_tab["table"].item(rows - 1, 0)
                try:
                    temperature_c = float(last_item.text()) if last_item is not None else 20.0
                except ValueError:
                    temperature_c = 20.0

        table = self.chamber_tab["table"]
        row = table.rowCount()
        table.blockSignals(True)
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(f"{float(temperature_c):.6g}"))
        table.setItem(row, 1, QTableWidgetItem(f"{float(hold_min):.6g}"))
        table.blockSignals(False)
        self.update_chamber_preview()

    def remove_selected_chamber_profile_points(self) -> None:
        table = self.chamber_tab["table"]
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        table.blockSignals(True)
        for row in rows:
            table.removeRow(row)
        table.blockSignals(False)
        self.update_chamber_preview()

    def on_chamber_enabled_changed(self, *args) -> None:
        if not hasattr(self, "chamber_tab"):
            return

        enabled = self.chamber_tab["enabled"].isChecked()
        for key in ("port", "refresh", "table", "add", "remove", "preview_plot"):
            self.chamber_tab[key].setEnabled(enabled)
        self.update_chamber_preview()

    def collect_chamber_profile(self, show_errors: bool = True) -> list[ChamberProfilePoint] | None:
        table = self.chamber_tab["table"]
        points: list[ChamberProfilePoint] = []
        errors: list[str] = []

        for row in range(table.rowCount()):
            temp_item = table.item(row, 0)
            hold_item = table.item(row, 1)
            temp_text = temp_item.text().strip() if temp_item is not None else ""
            hold_text = hold_item.text().strip() if hold_item is not None else ""

            try:
                temperature_c = float(temp_text)
            except ValueError:
                errors.append(f"Row {row + 1}: temperature must be numeric.")
                continue

            try:
                hold_min = float(hold_text)
            except ValueError:
                errors.append(f"Row {row + 1}: hold duration must be numeric.")
                continue

            if not (CHAMBER_MIN_TEMP_C <= temperature_c <= CHAMBER_MAX_TEMP_C):
                errors.append(
                    f"Row {row + 1}: temperature must be between "
                    f"{CHAMBER_MIN_TEMP_C:.0f} and {CHAMBER_MAX_TEMP_C:.0f} C."
                )
            if hold_min < 0:
                errors.append(f"Row {row + 1}: hold duration must be zero or greater.")
            points.append(ChamberProfilePoint(temperature_c=temperature_c, hold_s=hold_min * 60.0))

        if not points:
            errors.append("Add at least one thermal chamber temperature step.")

        if errors:
            if show_errors:
                QMessageBox.warning(self, "Thermal Chamber Program", "\n".join(errors))
            return None

        return points

    def update_chamber_preview(self, *args) -> None:
        if not hasattr(self, "chamber_tab"):
            return

        enabled = self.chamber_tab["enabled"].isChecked()
        points = self.collect_chamber_profile(show_errors=False)
        plot = self.chamber_tab["preview_plot"]
        plot.clear()
        self.chamber_tab["preview_item"] = None

        if not enabled:
            self.chamber_tab["summary"].setText("Thermal chamber program disabled.")
            return
        if not points:
            self.chamber_tab["summary"].setText("Enter valid temperature steps to preview the intended program.")
            return

        end_s = self.chamber_profile_preview_end_s(points)
        step_s = max(5.0, min(60.0, end_s / 240.0 if end_s > 0 else 5.0))
        sample_count = int(end_s / step_s) + 1
        elapsed_s = [min(index * step_s, end_s) for index in range(sample_count + 1)]
        if end_s not in elapsed_s:
            elapsed_s.append(end_s)

        elapsed_min = [value / 60.0 for value in elapsed_s]
        intended = [intended_chamber_temperature(points, value) for value in elapsed_s]
        self.chamber_tab["preview_item"] = plot.plot(
            elapsed_min,
            intended,
            pen=pg.mkPen(CHAMBER_INTENDED_COLOR, width=2),
            connect="all",
        )
        plot.setYRange(CHAMBER_MIN_TEMP_C - 5, CHAMBER_MAX_TEMP_C + 5, padding=0)
        self.chamber_tab["summary"].setText(
            f"{len(points)} temperature step(s), {chamber_profile_duration_s(points) / 60.0:.3g} min programmed hold/ramp time."
        )

    def chamber_profile_preview_end_s(self, points: list[ChamberProfilePoint]) -> float:
        if not points:
            return 0.0

        end_s = chamber_profile_duration_s(points)
        return max(end_s + 60.0, 60.0)

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

        # Color picker
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        color_button = QPushButton()
        color_button.setFixedWidth(80)
        color_button.setProperty("selected_color", config["color"])
        color_button.setStyleSheet(f"background-color: {config['color']};")
        color_button.clicked.connect(lambda: self.pick_channel_color(color_button))
        color_row.addWidget(color_button)
        color_row.addStretch()
        dialog_layout.addLayout(color_row)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        dialog_layout.addWidget(button_box)

        if dialog.exec() != QDialog.Accepted:
            return

        config["enabled"] = enabled_checkbox.isChecked()
        config["label"] = label_edit.text().strip() or f"TC08 CH{config['channel']}"
        config["tc_type"] = tc_type_combo.currentData()
        config["color"] = color_button.property("selected_color") or TC08_DEFAULT_ENABLED_COLOR
        if not config["enabled"]:
            config["last_temp"] = None

        self.refresh_tc08_buttons()
        self.update_tc08_summary()

    def pick_channel_color(self, button: QPushButton) -> None:
        """Open color picker dialog and update button color."""
        current_color = QColor(button.property("selected_color") or TC08_DEFAULT_ENABLED_COLOR)
        color = QColorDialog.getColor(current_color, self, "Pick Channel Color")
        if color.isValid():
            color_hex = color.name()
            button.setProperty("selected_color", color_hex)
            button.setStyleSheet(f"background-color: {color_hex};")

    def refresh_tc08_buttons(self) -> None:
        """Update the graphics view overlays based on channel colors."""
        channel_colors = {}
        for config in self.tc08_tab["configs"]:
            channel_colors[config["channel"]] = (
                config["color"] if config["enabled"] else TC08_DISABLED_COLOR
            )
        
        self.tc08_tab["graphics_view"].update_channel_state(channel_colors)
        self.update_tc08_summary()

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

        self.tc08_tab["summary"].setText("\n".join(summary_lines))

    def on_tc08_master_enabled_changed(self) -> None:
        self.update_tc08_summary()

    def on_plot_sample_count_changed(self, new_count: int) -> None:
        """Handle when user changes the plot sample count."""
        for buffer in self.plot_tab["tc_buffers"].values():
            buffer.set_max_size(new_count)
        for buffer in self.plot_tab["dmm_buffers"].values():
            buffer.set_max_size(new_count)
        self.plot_tab["chamber_actual_buffer"].set_max_size(new_count)
        self.plot_tab["chamber_intended_buffer"].set_max_size(new_count)
        self.rescale_tc_plot_y_axis()

    def rescale_tc_plot_y_axis(self) -> None:
        """Scale the temperature plot across all enabled TC-08 channels."""
        enabled_channels = {
            config["channel"] for config in self.tc08_tab["configs"] if config["enabled"]
        }
        values = []
        for channel, buffer in self.plot_tab["tc_buffers"].items():
            if channel not in enabled_channels:
                continue
            _, temps = buffer.get_data()
            values.extend(value for value in temps if not math.isnan(value))

        if not values:
            return

        self.plot_tab["tc_plot"].setYRange(min(values) - 5, max(values) + 5, padding=0)

    def update_tc_plot(self, channel: int, temp: float, timestamp: float) -> None:
        """Update the temperature plot with new data."""
        config = next((c for c in self.tc08_tab["configs"] if c["channel"] == channel), None)
        label = config["label"] if config else f"TC08 CH{channel}"
        color = config["color"] if config else TC08_DEFAULT_ENABLED_COLOR

        if channel not in self.plot_tab["tc_buffers"]:
            # First time seeing this channel
            self.plot_tab["tc_buffers"][channel] = CircularBuffer(
                self.plot_tab["sample_spinbox"].value()
            )
            pen = pg.mkPen(color, width=2)
            plot_item = self.plot_tab["tc_plot"].plot(
                name=label,
                pen=pen,
                connect="all",
            )
            self.plot_tab["tc_plot_items"][channel] = plot_item
        else:
            self.plot_tab["tc_plot_items"][channel].setPen(pg.mkPen(color, width=2))
        
        self.plot_tab["tc_buffers"][channel].append(temp, timestamp)
        timestamps, temps = self.plot_tab["tc_buffers"][channel].get_data()
        
        if timestamps:
            self.rescale_tc_plot_y_axis()
            self.plot_tab["tc_plot_items"][channel].setData(timestamps, temps, connect="all")

    def update_dmm_plot(self, dmm_index: int, value: float, timestamp: float, label: str) -> None:
        """Update the DMM plot with new data."""
        if dmm_index not in self.plot_tab["dmm_buffers"]:
            # First time seeing this DMM
            self.plot_tab["dmm_buffers"][dmm_index] = CircularBuffer(
                self.plot_tab["sample_spinbox"].value()
            )
            pen = pg.mkPen(pg.intColor(dmm_index), width=2)
            plot_item = self.plot_tab["dmm_plot"].plot(
                name=label,
                pen=pen,
                connect="all",
            )
            self.plot_tab["dmm_plot_items"][dmm_index] = plot_item
        
        self.plot_tab["dmm_buffers"][dmm_index].append(value, timestamp)
        timestamps, values = self.plot_tab["dmm_buffers"][dmm_index].get_data()
        
        if timestamps:
            # Auto-scale Y axis with padding
            min_val = min(values)
            max_val = max(values)
            padding = max((max_val - min_val) * 0.1, abs(max_val) * 0.2)  # 20% of max or 10%
            self.plot_tab["dmm_plot"].setYRange(min_val - padding, max_val + padding)
            
            self.plot_tab["dmm_plot_items"][dmm_index].setData(timestamps, values, connect="all")

    def update_chamber_plot(self, actual_temp: float, intended_temp: float, timestamp: float) -> None:
        if self.plot_tab["chamber_actual_item"] is None:
            self.plot_tab["chamber_actual_item"] = self.plot_tab["chamber_plot"].plot(
                name="Actual",
                pen=pg.mkPen(CHAMBER_ACTUAL_COLOR, width=2),
                connect="all",
            )
        if self.plot_tab["chamber_intended_item"] is None:
            self.plot_tab["chamber_intended_item"] = self.plot_tab["chamber_plot"].plot(
                name="Intended",
                pen=pg.mkPen(CHAMBER_INTENDED_COLOR, width=2),
                connect="all",
            )

        self.plot_tab["chamber_actual_buffer"].append(actual_temp, timestamp)
        self.plot_tab["chamber_intended_buffer"].append(intended_temp, timestamp)

        actual_timestamps, actual_values = self.plot_tab["chamber_actual_buffer"].get_data()
        intended_timestamps, intended_values = self.plot_tab["chamber_intended_buffer"].get_data()

        if actual_timestamps:
            self.plot_tab["chamber_actual_item"].setData(actual_timestamps, actual_values, connect="all")
        if intended_timestamps:
            self.plot_tab["chamber_intended_item"].setData(intended_timestamps, intended_values, connect="all")

        values = [
            value
            for value in actual_values + intended_values
            if not math.isnan(value)
        ]
        if values:
            self.plot_tab["chamber_plot"].setYRange(min(values) - 5, max(values) + 5, padding=0)

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
        visible = int(self.dmm_count_combo.currentText())
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

        # Clear plot data
        self.plot_tab["tc_buffers"].clear()
        self.plot_tab["dmm_buffers"].clear()
        self.plot_tab["tc_plot_items"].clear()
        self.plot_tab["dmm_plot_items"].clear()
        self.plot_tab["tc_plot"].clear()
        self.plot_tab["dmm_plot"].clear()
        self.plot_tab["chamber_actual_buffer"].clear()
        self.plot_tab["chamber_intended_buffer"].clear()
        self.plot_tab["chamber_plot"].clear()
        self.plot_tab["chamber_actual_item"] = None
        self.plot_tab["chamber_intended_item"] = None

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

        # Collect enabled TC-08 channels (automatically enable if any channel is enabled)
        for channel_index, channel_config in enumerate(self.tc08_tab["configs"]):
            if channel_config["enabled"]:
                tc08_configs.append(
                    Tc08Config(
                        label=channel_config["label"],
                        channel=channel_config["channel"],
                        tc_type=channel_config["tc_type"],
                    )
                )

        chamber_config = None
        if self.chamber_tab["enabled"].isChecked():
            chamber_port = self.chamber_tab["port"].currentData()
            if not chamber_port:
                QMessageBox.warning(
                    self,
                    "Start Logging",
                    "Please detect and select a thermal chamber COM port.",
                )
                return

            chamber_profile = self.collect_chamber_profile(show_errors=True)
            if chamber_profile is None:
                return

            chamber_config = ChamberConfig(port=chamber_port, profile=chamber_profile)

        if not configs and not tc08_configs and chamber_config is None:
            QMessageBox.warning(
                self,
                "Start Logging",
                "Select at least one DMM, enable at least one TC-08 channel, or enable a thermal chamber program.",
            )
            return

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
            chamber_config=chamber_config,
        )

        self.worker_thread = QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker_thread.finished.connect(self.on_worker_finished)
        self.worker.status_updated.connect(self.append_status)
        self.worker.reading_updated.connect(self.on_reading_updated)
        self.worker.tc08_reading_updated.connect(self.on_tc08_reading_updated)
        self.worker.chamber_reading_updated.connect(self.on_chamber_reading_updated)
        self.worker.error_occurred.connect(self.on_worker_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)

        self.worker_thread.start()
        self.set_recording_indicator(True)
        self.start_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.dmm_count_combo.setEnabled(False)
        self.chamber_tab["widget"].setEnabled(False)
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
            
            # Update plot
            label = panel["label"].text().strip() or f"DMM {index + 1}"
            timestamp = datetime.now().timestamp()
            self.update_dmm_plot(index, value, timestamp, label)

    @Slot(int, float)
    def on_tc08_reading_updated(self, channel: int, value: float) -> None:
        channel_config = next(
            (config for config in self.tc08_tab["configs"] if config["channel"] == channel),
            None,
        )
        if channel_config is None:
            return

        channel_config["last_temp"] = value
        self.flash_recording_indicator()
        
        # Update plot
        timestamp = datetime.now().timestamp()
        self.update_tc_plot(channel, value, timestamp)

    @Slot(float, float)
    def on_chamber_reading_updated(self, actual_temp: float, intended_temp: float) -> None:
        self.flash_recording_indicator()
        timestamp = datetime.now().timestamp()
        self.update_chamber_plot(actual_temp, intended_temp, timestamp)

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
        self.chamber_tab["widget"].setEnabled(True)
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
