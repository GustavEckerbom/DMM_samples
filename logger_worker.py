"""Background logging worker for DMM, TC-08, and thermal chamber data."""

import csv
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from qt_compat import QObject, Signal, Slot

from dmm_comm import DmmInstrument, make_csv_filename, INTER_QUERY_DELAY_S
from pico_tc08_comm import Tc08Instrument
from thermo_comm import ThermalChamberInstrument


CHAMBER_HEATING_RATE_C_PER_S = 1.8 / 60.0
CHAMBER_COOLING_RATE_C_PER_S = 1.5 / 60.0


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


@dataclass
class ChamberProfilePoint:
    temperature_c: float
    hold_s: float


@dataclass
class ChamberConfig:
    port: str
    profile: list[ChamberProfilePoint]


def intended_chamber_temperature(profile: list[ChamberProfilePoint], elapsed_s: float) -> float:
    segments = chamber_profile_segments(profile)
    if not segments:
        return float("nan")

    current = segments[0]["start_temp_c"]
    for segment in segments:
        start_s = segment["start_s"]
        ramp_end_s = segment["ramp_end_s"]
        hold_end_s = segment["hold_end_s"]
        start_temp_c = segment["start_temp_c"]
        target_c = segment["target_c"]

        if elapsed_s < start_s:
            return current
        if elapsed_s <= ramp_end_s:
            return _move_temperature_toward_target(start_temp_c, target_c, elapsed_s - start_s)
        if elapsed_s <= hold_end_s:
            return target_c
        current = target_c

    return segments[-1]["target_c"]


def chamber_command_temperature(profile: list[ChamberProfilePoint], elapsed_s: float) -> float:
    segments = chamber_profile_segments(profile)
    if not segments:
        return float("nan")

    target_c = segments[0]["target_c"]
    for segment in segments:
        if elapsed_s >= segment["start_s"]:
            target_c = segment["target_c"]
        else:
            break
    return target_c


def chamber_profile_command_times(profile: list[ChamberProfilePoint]) -> list[tuple[float, float]]:
    return [(segment["start_s"], segment["target_c"]) for segment in chamber_profile_segments(profile)]


def chamber_profile_duration_s(profile: list[ChamberProfilePoint]) -> float:
    segments = chamber_profile_segments(profile)
    if not segments:
        return 0.0
    return segments[-1]["hold_end_s"]


def chamber_profile_segments(profile: list[ChamberProfilePoint]) -> list[dict[str, float]]:
    if not profile:
        return []

    segments: list[dict[str, float]] = []
    current_temp = profile[0].temperature_c
    current_time = 0.0

    for point in profile:
        ramp_s = _ramp_duration_s(current_temp, point.temperature_c)
        ramp_end_s = current_time + ramp_s
        hold_end_s = ramp_end_s + point.hold_s
        segments.append(
            {
                "start_s": current_time,
                "ramp_end_s": ramp_end_s,
                "hold_end_s": hold_end_s,
                "start_temp_c": current_temp,
                "target_c": point.temperature_c,
            }
        )
        current_temp = point.temperature_c
        current_time = hold_end_s

    return segments


def _ramp_duration_s(start_c: float, target_c: float) -> float:
    if start_c == target_c:
        return 0.0

    rate = CHAMBER_HEATING_RATE_C_PER_S if target_c > start_c else CHAMBER_COOLING_RATE_C_PER_S
    return abs(target_c - start_c) / rate


def _move_temperature_toward_target(current_c: float, target_c: float, elapsed_s: float) -> float:
    if current_c == target_c or elapsed_s <= 0:
        return current_c

    rate = CHAMBER_HEATING_RATE_C_PER_S if target_c > current_c else CHAMBER_COOLING_RATE_C_PER_S
    delta = rate * elapsed_s
    if target_c > current_c:
        return min(target_c, current_c + delta)
    return max(target_c, current_c - delta)


class DmmLoggerWorker(QObject):
    status_updated = Signal(str)
    reading_updated = Signal(int, float)
    tc08_reading_updated = Signal(int, float)
    chamber_reading_updated = Signal(float, float)
    finished = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        configs: List[DmmConfig],
        tc08_configs: list[Tc08Config],
        csv_path: str,
        sample_period_s: float = 1.0,
        chamber_config: ChamberConfig | None = None,
    ):
        super().__init__()
        self.configs = configs
        self.tc08_configs = tc08_configs
        self.csv_path = csv_path
        self.sample_period_s = sample_period_s
        self.chamber_config = chamber_config
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

        tc08_instrument = None
        chamber_instrument = None
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

            if self.chamber_config is not None:
                self.status_updated.emit(f"Opening thermal chamber on {self.chamber_config.port}")
                chamber_instrument = ThermalChamberInstrument(self.chamber_config.port)
                chamber_instrument.open()
                if self.chamber_config.profile:
                    first_point = self.chamber_config.profile[0]
                    chamber_instrument.set_temperature(first_point.temperature_c)
                    self.status_updated.emit(
                        f"Thermal chamber setpoint: {first_point.temperature_c:.1f} C"
                    )
                self.status_updated.emit("Thermal chamber ready")

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
                if self.chamber_config is not None:
                    writer.writerow(["Thermal chamber COM port", self.chamber_config.port])
                    for point in self.chamber_config.profile:
                        writer.writerow(
                            [
                                "Thermal chamber temperature step",
                                f"{point.temperature_c:.6g} C",
                                f"{point.hold_s / 60.0:.6g} min hold",
                            ]
                        )
                writer.writerow([])
                header = ["timestamp", "elapsed_s"]
                header.extend([config.label or f"DMM {index + 1}" for index, config in enumerate(self.configs)])
                header.extend([config.label or f"TC08 ch{config.channel}" for config in self.tc08_configs])
                if self.chamber_config is not None:
                    header.extend(["Chamber current C", "Chamber set C", "Chamber intended C"])
                writer.writerow(header)

                self.status_updated.emit(f"Logging to {self.csv_path}")
                next_chamber_command_index = 1 if self.chamber_config is not None else 0
                chamber_command_times = (
                    chamber_profile_command_times(self.chamber_config.profile)
                    if self.chamber_config is not None
                    else []
                )

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
                            self.tc08_reading_updated.emit(config.channel, tc08_value)
                            row.append(f"{tc08_value:.9f}" if not math.isnan(tc08_value) else "")

                    if chamber_instrument is not None and self.chamber_config is not None:
                        while (
                            next_chamber_command_index < len(chamber_command_times)
                            and elapsed_s >= chamber_command_times[next_chamber_command_index][0]
                        ):
                            _, target_c = chamber_command_times[next_chamber_command_index]
                            chamber_instrument.set_temperature(target_c)
                            self.status_updated.emit(
                                f"Thermal chamber setpoint: {target_c:.1f} C"
                            )
                            next_chamber_command_index += 1

                        set_temp = chamber_command_temperature(self.chamber_config.profile, elapsed_s)
                        intended_temp = intended_chamber_temperature(self.chamber_config.profile, elapsed_s)
                        actual_temp = chamber_instrument.read_temperature()
                        self.chamber_reading_updated.emit(actual_temp, intended_temp)
                        row.append(f"{actual_temp:.9f}" if not math.isnan(actual_temp) else "")
                        row.append(f"{set_temp:.9f}" if not math.isnan(set_temp) else "")
                        row.append(f"{intended_temp:.9f}" if not math.isnan(intended_temp) else "")

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
            if chamber_instrument is not None:
                chamber_instrument.close()
            self.finished.emit()
