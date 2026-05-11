import csv
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import QObject, Signal, Slot

from dmm_comm import DmmInstrument, make_csv_filename, INTER_QUERY_DELAY_S
from pico_tc08_comm import Tc08Instrument


@dataclass
class DmmConfig:
    label: str
    port: str
    measurement_type: str
    measurement_range: str
    nplc: str
    idn: str | None = None


@dataclass
class Tc08Config:
    label: str
    channel: int
    tc_type: str


class DmmLoggerWorker(QObject):
    status_updated = Signal(str)
    reading_updated = Signal(int, float)
    tc08_reading_updated = Signal(int, float)
    finished = Signal()
    error_occurred = Signal(str)

    def __init__(self, configs: List[DmmConfig], tc08_configs: list[Tc08Config], csv_path: str, sample_period_s: float = 1.0):
        super().__init__()
        self.configs = configs
        self.tc08_configs = tc08_configs
        self.csv_path = csv_path
        self.sample_period_s = sample_period_s
        self._stop_requested = False

    @Slot()
    def stop(self) -> None:
        self._stop_requested = True
        self.status_updated.emit("Stop requested")

    @Slot()
    def run(self) -> None:
        instruments: list[DmmInstrument] = []
        meter_ids: list[str] = []
        t0 = time.time()

        tc08_unit = None
        tc08_temp = None
        tc08_handle = None
        try:
            for config in self.configs:
                self.status_updated.emit(f"Opening {config.label} on {config.port}")
                instrument = DmmInstrument(config.port)
                instrument.open()
                meter_id = instrument.identify(config.label)
                instrument.configure(config.measurement_type, config.measurement_range, config.nplc)
                instruments.append(instrument)
                meter_ids.append(meter_id)
                self.status_updated.emit(f"{config.label} ready: {meter_id}")

            tc08_instrument = None
            if self.tc08_configs:
                self.status_updated.emit("Opening Pico TC-08")
                tc08_instrument = Tc08Instrument()
                tc08_instrument.open()
                channel_map = {config.channel: config.tc_type for config in self.tc08_configs}
                tc08_instrument.configure_channels(channel_map)
                for config in self.tc08_configs:
                    self.status_updated.emit(
                        f"{config.label} ready: channel {config.channel} ({config.tc_type})"
                    )

            os.makedirs(os.path.dirname(self.csv_path) or ".", exist_ok=True)
            with open(self.csv_path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(["Start time", datetime.now().isoformat(sep=" ", timespec="seconds")])
                writer.writerow(["Sample period (s)", self.sample_period_s])
                for index, config in enumerate(self.configs, start=1):
                    writer.writerow([f"DMM {index} COM port", config.port])
                    writer.writerow([f"DMM {index} measurement", config.measurement_type])
                    writer.writerow([f"DMM {index} range", config.measurement_range])
                    writer.writerow([f"DMM {index} NPLC", config.nplc])
                    writer.writerow([f"DMM {index} meter ID", meter_ids[index - 1]])
                    writer.writerow([f"DMM {index} label", config.label])
                if self.tc08_configs:
                    for config in self.tc08_configs:
                        writer.writerow([f"TC08 channel", config.channel])
                        writer.writerow([f"TC08 type", config.tc_type])
                        writer.writerow([f"TC08 label", config.label])
                writer.writerow([])
                header = ["timestamp", "elapsed_s"]
                header.extend([config.label or f"DMM {index + 1}" for index, config in enumerate(self.configs)])
                header.extend([config.label or f"TC08 ch{config.channel}" for config in self.tc08_configs])
                writer.writerow(header)

                self.status_updated.emit(f"Logging to {self.csv_path}")
                while not self._stop_requested:
                    loop_start = time.time()
                    timestamp = datetime.now().isoformat(sep=" ", timespec="seconds")
                    elapsed_s = loop_start - t0

                    row = [timestamp, f"{elapsed_s:.3f}"]
                    for i, instrument in enumerate(instruments):
                        measurement = instrument.read_measurement()
                        self.reading_updated.emit(i, measurement)
                        row.append(f"{measurement:.9f}")
                        if i < len(instruments) - 1:
                            time.sleep(INTER_QUERY_DELAY_S)

                    if tc08_instrument is not None:
                        temps = tc08_instrument.read_temperatures()
                        for i, config in enumerate(self.tc08_configs):
                            tc08_value = temps.get(config.channel, float("nan"))
                            self.tc08_reading_updated.emit(i, tc08_value)
                            row.append(f"{tc08_value:.9f}" if not math.isnan(tc08_value) else "")

                    writer.writerow(row)
                    csv_file.flush()

                    elapsed_loop = time.time() - loop_start
                    sleep_time = self.sample_period_s - elapsed_loop
                    if sleep_time > 0:
                        time.sleep(sleep_time)

        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            for instrument in instruments:
                instrument.close()
            if tc08_instrument is not None:
                tc08_instrument.close()
            self.finished.emit()
