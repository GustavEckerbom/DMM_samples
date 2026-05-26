"""
thermo_comm.py

Small abstraction layer for a serial-controlled thermal chamber.

Designed to feel similar to dmm_comm.py and pico_tc08_comm.py:
    chamber = ThermalChamberInstrument("COM5")
    chamber.open()
    print(chamber.identify())
    chamber.set_temperature(-40)
    print(chamber.read_temperature())
    chamber.close()

The command strings are kept from the original chamber script:
    $00I  -> read current/set temperature information
    $00E  -> set chamber temperature and enable/disable control
    $00U  -> set up/down gradients
    $00F  -> read errors
    $00Q  -> clear/quit errors

The GUI uses detect_chamber_ports() to probe serial ports with $00I and only
offer ports that look like a thermal chamber.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import serial
import serial.tools.list_ports


DEFAULT_PORT = "COM5"
DEFAULT_BAUD = 9600
DEFAULT_TIMEOUT_S = 1.0
DEFAULT_SLEEP_S = 10.0
DEFAULT_MIN_STABILIZE_MIN = 30.0
DEFAULT_MAX_STABILIZE_MIN = 45.0
DEFAULT_STABILITY_BAND_C = 0.4
OFF_TEMPERATURE_C = 20.0

TEMPERATURE_PATTERN = re.compile(r"[-+]?\d+\.\d+")


@dataclass
class ChamberProbeResult:
    port: str
    response: str


class ThermalChamberError(RuntimeError):
    pass


class ThermalChamberLike(Protocol):
    setpoint_c: float

    def open(self) -> None:
        ...

    def close(self) -> None:
        ...

    def identify(self) -> str:
        ...

    def read_temperature(self) -> float:
        ...

    def set_temperature(self, temperature_c: float) -> str:
        ...

    def turn_off(self) -> str:
        ...


class ThermalChamberSimulator:
    """In-memory stand-in with the same public surface as the serial chamber."""

    def __init__(
        self,
        initial_temperature_c: float = 20.0,
        min_stabilize_min: float = DEFAULT_MIN_STABILIZE_MIN,
        max_stabilize_min: float = DEFAULT_MAX_STABILIZE_MIN,
        sleep_s: float = DEFAULT_SLEEP_S,
    ):
        self.temperature_c = float(initial_temperature_c)
        self.setpoint_c = float(initial_temperature_c)
        self.min_stabilize_min = float(min_stabilize_min)
        self.max_stabilize_min = float(max_stabilize_min)
        self.sleep_s = float(sleep_s)
        self.is_open = False

    def open(
        self,
        com_port: Optional[str] = None,
        min_time: Optional[float] = None,
        max_time: Optional[float] = None,
    ) -> None:
        if min_time is not None:
            self.min_stabilize_min = float(min_time)
        if max_time is not None:
            self.max_stabilize_min = float(max_time)
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def __enter__(self) -> "ThermalChamberSimulator":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def identify(self) -> str:
        return "Thermal chamber simulator"

    def read_temperature(self) -> float:
        return self.temperature_c

    def set_temperature(self, temperature_c: float) -> str:
        self.setpoint_c = float(temperature_c)
        self.temperature_c = self.setpoint_c
        return "SIM OK"

    def set_gradient(self, up: float, down: float) -> str:
        return f"SIM GRADIENT {up:g} {down:g}"

    def read_error(self) -> str:
        return "SIM NO ERROR"

    def clear_error(self) -> str:
        return "SIM OK"

    def turn_off(self) -> str:
        return self.set_temperature(OFF_TEMPERATURE_C)

    def wait_until_stable(
        self,
        min_time_min: Optional[float] = None,
        max_time_min: Optional[float] = None,
        sleep_s: Optional[float] = None,
        stability_band_c: float = DEFAULT_STABILITY_BAND_C,
    ) -> float:
        return self.temperature_c

    # Backward-compatible names from the original script.
    get_temp = read_temperature
    set_temp = set_temperature
    quit_error = clear_error
    wait = wait_until_stable


class ThermalChamberInstrument:
    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baud: int = DEFAULT_BAUD,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        min_stabilize_min: float = DEFAULT_MIN_STABILIZE_MIN,
        max_stabilize_min: float = DEFAULT_MAX_STABILIZE_MIN,
        sleep_s: float = DEFAULT_SLEEP_S,
    ):
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self.min_stabilize_min = float(min_stabilize_min)
        self.max_stabilize_min = float(max_stabilize_min)
        self.sleep_s = float(sleep_s)
        self.serial: Optional[serial.Serial] = None
        self.setpoint_c = OFF_TEMPERATURE_C

    def open(
        self,
        com_port: Optional[str] = None,
        min_time: Optional[float] = None,
        max_time: Optional[float] = None,
    ) -> None:
        if com_port is not None:
            self.port = com_port
        if min_time is not None:
            self.min_stabilize_min = float(min_time)
        if max_time is not None:
            self.max_stabilize_min = float(max_time)

        if self.serial is not None and self.serial.is_open:
            return

        self.serial = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout_s,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()

    def close(self) -> None:
        if self.serial is not None and self.serial.is_open:
            self.serial.close()
        self.serial = None

    def __enter__(self) -> "ThermalChamberInstrument":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def write_cmd(self, cmd: str) -> None:
        assert self.serial is not None
        self.serial.write((cmd + "\r\n").encode("ascii"))
        self.serial.flush()

    def read_resp(self) -> str:
        assert self.serial is not None
        return self.serial.readline().decode("ascii", errors="replace").strip()

    def query(self, cmd: str) -> str:
        self.write_cmd(cmd)
        return self.read_resp()

    def identify(self) -> str:
        response = self.query("$00I")
        if not response:
            raise ThermalChamberError(f"{self.port}: chamber did not respond to $00I")
        return response

    def read_temperature(self) -> float:
        response = self.query("$00I")
        temperatures = _parse_temperatures(response)
        if len(temperatures) >= 2:
            return temperatures[1]
        if len(temperatures) == 1:
            return temperatures[0]
        raise ThermalChamberError(f"{self.port}: could not parse chamber temperature from {response!r}")

    def set_temperature(self, temperature_c: float) -> str:
        self.setpoint_c = float(temperature_c)
        return self.query(_format_temperature_command(self.setpoint_c, enabled=True))

    def set_gradient(self, up: float, down: float) -> str:
        return self.query(
            f"$00U {_format_protocol_temperature(up)} {_format_protocol_temperature(down)} 0101000000000000"
        )

    def read_error(self) -> str:
        return self.query("$00F")

    def clear_error(self) -> str:
        return self.query("$00Q")

    def turn_off(self) -> str:
        self.setpoint_c = OFF_TEMPERATURE_C
        return self.query(_format_temperature_command(OFF_TEMPERATURE_C, enabled=False))

    def wait_until_stable(
        self,
        min_time_min: Optional[float] = None,
        max_time_min: Optional[float] = None,
        sleep_s: Optional[float] = None,
        stability_band_c: float = DEFAULT_STABILITY_BAND_C,
    ) -> float:
        """
        Wait until the chamber reaches the setpoint and remains within the band.

        The original script reset the minimum wait whenever the temperature moved
        outside the stability band. This preserves that behavior, with an overall
        max wait to avoid blocking forever.
        """
        min_time_min = self.min_stabilize_min if min_time_min is None else min_time_min
        max_time_min = self.max_stabilize_min if max_time_min is None else max_time_min
        sleep_s = self.sleep_s if sleep_s is None else sleep_s

        deadline = time.monotonic() + max_time_min * 60.0
        stable_until = time.monotonic() + min_time_min * 60.0
        last_temperature = self.read_temperature()

        while time.monotonic() < deadline:
            last_temperature = self.read_temperature()
            if abs(last_temperature - self.setpoint_c) > stability_band_c:
                stable_until = time.monotonic() + min_time_min * 60.0
            elif time.monotonic() >= stable_until:
                return last_temperature

            time.sleep(sleep_s)

        raise ThermalChamberError(
            f"{self.port}: chamber did not stabilize at {self.setpoint_c:.1f} C "
            f"within {max_time_min:g} minutes. Last temperature: {last_temperature:.1f} C"
        )

    # Backward-compatible names from the original script.
    get_temp = read_temperature
    set_temp = set_temperature
    quit_error = clear_error
    wait = wait_until_stable


# Backward-compatible class names from the original script.
TemperatureChamber = ThermalChamberInstrument
TemperatureChamberSimulate = ThermalChamberSimulator


def _format_protocol_temperature(temperature_c: float) -> str:
    return f"{temperature_c:04.0f}.0"


def _format_temperature_command(temperature_c: float, enabled: bool) -> str:
    control_mask = "0101000000000000" if enabled else "0000000000000000"
    return f"$00E {_format_protocol_temperature(temperature_c)} {control_mask}"


def _parse_temperatures(response: str) -> list[float]:
    return [float(value) for value in TEMPERATURE_PATTERN.findall(response)]


def list_serial_ports() -> list[str]:
    return [port.device for port in serial.tools.list_ports.comports()]


def probe_chamber_port(port: str, baud: int = DEFAULT_BAUD, timeout_s: float = 1.0) -> Optional[str]:
    instrument = ThermalChamberInstrument(port=port, baud=baud, timeout_s=timeout_s)
    try:
        instrument.open()
        response = instrument.identify()
        return response if _parse_temperatures(response) else None
    except Exception:
        return None
    finally:
        instrument.close()


def detect_chamber_ports(timeout_s: float = 1.0) -> list[ChamberProbeResult]:
    results: list[ChamberProbeResult] = []
    for port in list_serial_ports():
        response = probe_chamber_port(port, timeout_s=timeout_s)
        if response:
            results.append(ChamberProbeResult(port=port, response=response))
    return results


def log_temperature(chamber: ThermalChamberLike, log_path: str = "temperature_log.txt") -> None:
    with Path(log_path).open("a", encoding="utf-8") as logfile:
        logfile.write(f"{time.strftime('%X')} {chamber.read_temperature():.1f}C\n")


def log_temperature_command(
    temperature_c: float,
    response: str,
    log_path: str = "temperature_log.txt",
) -> None:
    with Path(log_path).open("a", encoding="utf-8") as logfile:
        logfile.write(
            f"{time.strftime('%X')} Setting temp to {temperature_c:.1f}C, "
            f"Chamber returned {response}\n"
        )


def log_generic(message: str, log_path: str = "temperature_log.txt") -> None:
    with Path(log_path).open("a", encoding="utf-8") as logfile:
        logfile.write(message)


def main() -> None:
    temp_low = -40.0
    temp_high = 40.0
    current_setpoint = temp_high

    with ThermalChamberInstrument(port=DEFAULT_PORT) as chamber:
        chamber.clear_error()
        chamber.clear_error()

        try:
            while True:
                current_setpoint = temp_low if current_setpoint == temp_high else temp_high
                chamber.set_gradient(1000, 1000)
                response = chamber.set_temperature(current_setpoint)
                log_temperature_command(current_setpoint, response)
                chamber.read_error()
                chamber.clear_error()
                chamber.wait_until_stable(min_time_min=45, max_time_min=45)
                log_temperature(chamber)

                start = time.time()
                while time.time() - start < 20 * 60:
                    current_temp = chamber.read_temperature()
                    time_left = 20 * 60 - (time.time() - start)
                    log_generic(
                        f"Soaking, current temp {current_temp:.1f}, "
                        f"setpoint {current_setpoint:.1f}. Time left: {time_left:.0f}\n"
                    )
                    time.sleep(60)
                log_generic("Soak cycle complete.\n")
        finally:
            chamber.turn_off()


if __name__ == "__main__":
    main()
