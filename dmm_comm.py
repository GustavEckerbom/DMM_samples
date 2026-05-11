import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import serial
import serial.tools.list_ports

SCPI_VENDOR_PREFIXES = ("HEWLETT-PACKARD", "HP", "AGILENT", "KEYSIGHT")


@dataclass
class DmmProbeResult:
    port: str
    idn: str

DEFAULT_BAUD = 9600
DEFAULT_TIMEOUT_S = 5.0
STARTUP_RETRIES = 3
INTER_QUERY_DELAY_S = 0.05

VOLTAGE_RANGES = ["0.1", "1", "10", "100", "1000"]
CURRENT_RANGES = ["0.01", "0.1", "1", "3"]
NPLC_OPTIONS = ["0.02", "0.2", "1", "10", "100"]


def make_csv_filename(start_dt: datetime) -> str:
    stamp = start_dt.strftime("%Y-%m-%d_%H-%M-%S")
    return f"dmm_log_{stamp}.csv"


def open_file_with_default_app(path: str) -> None:
    try:
        import os
        import subprocess
        import sys

        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception:
        pass


class DmmInstrument:
    def __init__(self, port: str, baud: int = DEFAULT_BAUD, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self.serial: Optional[serial.Serial] = None
        self.measurement_type: Optional[str] = None

    def open(self) -> None:
        self.serial = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO,
            timeout=self.timeout_s,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        time.sleep(0.5)
        assert self.serial is not None
        self.serial.dtr = True
        self.serial.rts = True
        time.sleep(0.2)
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()

    def close(self) -> None:
        if self.serial is not None and self.serial.is_open:
            self.serial.close()
            self.serial = None

    def write_cmd(self, cmd: str) -> None:
        assert self.serial is not None
        self.serial.write((cmd + "\n").encode("ascii"))
        self.serial.flush()

    def read_resp(self) -> str:
        assert self.serial is not None
        return self.serial.readline().decode("ascii", errors="replace").strip()

    def query(self, cmd: str) -> str:
        self.write_cmd(cmd)
        return self.read_resp()

    def get_error(self) -> str:
        return self.query(":SYST:ERR?")

    def clear_errors(self) -> None:
        self.write_cmd("*CLS")
        time.sleep(0.05)

    def expect_numeric_query(self, cmd: str, label: str) -> float:
        resp = self.query(cmd)
        if not resp:
            err = self.get_error()
            raise RuntimeError(f"{label}: empty response to {cmd}. Meter error: {err}")

        try:
            return float(resp)
        except ValueError:
            err = self.get_error()
            raise RuntimeError(f"{label}: non-numeric response {resp!r}. Meter error: {err}")

    def identify(self, label: str) -> str:
        last_err = ""
        assert self.serial is not None

        for attempt in range(1, STARTUP_RETRIES + 1):
            try:
                self.write_cmd(":SYST:REM")
                time.sleep(0.1)

                idn = self.query("*IDN?")
                if idn:
                    self.clear_errors()
                    return idn

                last_err = self.get_error()
                time.sleep(0.2)
            except Exception as e:
                last_err = str(e)
                time.sleep(0.2)

        raise RuntimeError(
            f"{label}: failed to identify meter after {STARTUP_RETRIES} attempts. Last error: {last_err}"
        )

    def configure(self, measurement_type: str, measurement_range: str, nplc: str) -> None:
        self.clear_errors()
        if measurement_type == "voltage":
            self.write_cmd(f":CONF:VOLT:DC {measurement_range},DEF")
            self.write_cmd(f":VOLT:DC:NPLC {nplc}")
        elif measurement_type == "current":
            self.write_cmd(f":CONF:CURR:DC {measurement_range},DEF")
            self.write_cmd(f":CURR:DC:NPLC {nplc}")
        else:
            raise ValueError(f"Unsupported measurement type: {measurement_type}")

        self.write_cmd(":TRIG:SOUR IMM")
        self.write_cmd(":SAMP:COUN 1")
        time.sleep(0.1)

        err = self.get_error()
        if not err.startswith("+0"):
            raise RuntimeError(f"{measurement_type.title()} meter configuration error: {err}")

        self.measurement_type = measurement_type

    def read_measurement(self) -> float:
        if self.measurement_type == "voltage":
            return self.expect_numeric_query(":READ?", "Voltage meter")
        if self.measurement_type == "current":
            return self.expect_numeric_query(":READ?", "Current meter")
        raise RuntimeError("Measurement type is not configured")


def _looks_like_scpi_idn(resp: str) -> bool:
    if not resp:
        return False

    parts = [part.strip() for part in resp.split(",")]
    if len(parts) < 2 or not parts[0]:
        return False

    vendor = parts[0].upper().replace(" ", "-")
    if any(vendor.startswith(prefix) for prefix in SCPI_VENDOR_PREFIXES):
        return True

    return True


def probe_dmm_port(port: str, baud: int = DEFAULT_BAUD, timeout_s: float = 1.0) -> Optional[str]:
    instrument = DmmInstrument(port, baud=baud, timeout_s=timeout_s)
    try:
        instrument.open()
        instrument.write_cmd("*IDN?")
        idn = instrument.read_resp()
        return idn if _looks_like_scpi_idn(idn) else None
    except Exception:
        return None
    finally:
        instrument.close()


def detect_dmm_ports(timeout_s: float = 1.0) -> list[DmmProbeResult]:
    results: list[DmmProbeResult] = []
    for port in list_serial_ports():
        idn = probe_dmm_port(port, timeout_s=timeout_s)
        if idn:
            results.append(DmmProbeResult(port=port, idn=idn))
    return results


def list_serial_ports() -> list[str]:
    return [port.device for port in serial.tools.list_ports.comports()]
