"""
pico_tc08_comm.py

Small abstraction layer for Pico USB TC-08 thermocouple logger.

Designed to feel similar to dmm_comm.py:
    tc08 = Tc08Instrument()
    tc08.open()
    print(tc08.identify())
    tc08.configure_channel(1, "K")
    temp = tc08.read_temperature(1)
    tc08.close()

This implementation uses Get Single mode via PicoSDK:
    usb_tc08_open_unit()
    usb_tc08_set_mains()
    usb_tc08_set_channel()
    usb_tc08_get_single()
    usb_tc08_close_unit()

Requirements:
    - PicoSDK / PicoLog installed
    - usbtc08.dll available on PATH, or pass dll_path explicitly
    - Python bitness must match the DLL bitness, usually 64-bit Python with 64-bit PicoSDK

Notes:
    - Channel 0 is cold junction / ambient inside logger.
    - Channels 1..8 are thermocouple inputs.
    - Disabled channels return NaN from the driver.
"""

from __future__ import annotations

import ctypes
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


TC08_UNITS_CENTIGRADE = 0
TC08_UNITS_FAHRENHEIT = 1
TC08_UNITS_KELVIN = 2
TC08_UNITS_RANKINE = 3

VALID_TC_TYPES = {"B", "E", "J", "K", "N", "R", "S", "T", "X", " "}
VALID_CHANNELS = range(0, 9)
_DLL_DIRECTORY_HANDLES = []

# Values from the programmer's guide.
TC08_ERROR_CODES = {
    0: "USBTC08_ERROR_OK",
    1: "USBTC08_ERROR_OS_NOT_SUPPORTED",
    2: "USBTC08_ERROR_NO_CHANNELS_SET",
    3: "USBTC08_ERROR_INVALID_PARAMETER",
    4: "USBTC08_ERROR_VARIANT_NOT_SUPPORTED",
    5: "USBTC08_ERROR_INCORRECT_MODE",
    6: "USBTC08_ERROR_ENUMERATION_INCOMPLETE",
    7: "USBTC08_ERROR_NOT_RESPONDING",
    8: "USBTC08_ERROR_FW_FAIL",
    9: "USBTC08_ERROR_CONFIG_FAIL",
    10: "USBTC08_ERROR_NOT_FOUND",
    11: "USBTC08_ERROR_THREAD_FAIL",
    12: "USBTC08_ERROR_PIPE_INFO_FAIL",
    13: "USBTC08_ERROR_NOT_CALIBRATED",
    14: "USBTC08_ERROR_PICOPP_TOO_OLD",
    15: "USBTC08_ERROR_COMMUNICATION",
}


@dataclass
class Tc08ProbeResult:
    handle: int
    info: str


class Tc08Error(RuntimeError):
    pass


def _default_dll_candidates() -> list[str]:
    module_dir = Path(__file__).resolve().parent
    candidates = [
        str(module_dir / "usbtc08.dll"),
        "usbtc08.dll",
    ]

    program_files = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]

    possible_subdirs = [
        r"Pico Technology\SDK\lib\usbtc08.dll",
        r"Pico Technology\PicoSDK\lib\usbtc08.dll",
        r"Pico Technology\PicoLog 6\usbtc08.dll",
    ]

    for root in program_files:
        if not root:
            continue
        for subdir in possible_subdirs:
            candidates.append(str(Path(root) / subdir))

    return candidates


def _load_tc08_dll(dll_path: Optional[str] = None) -> ctypes.WinDLL:
    def load_with_directory(candidate: str) -> ctypes.WinDLL:
        candidate_path = Path(candidate)
        if candidate_path.parent != Path(".") and hasattr(os, "add_dll_directory"):
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(candidate_path.parent)))
        return ctypes.WinDLL(str(candidate_path))

    if dll_path:
        return load_with_directory(dll_path)

    last_error: Optional[Exception] = None
    for candidate in _default_dll_candidates():
        try:
            return load_with_directory(candidate)
        except Exception as exc:
            last_error = exc

    raise Tc08Error(
        "Could not load usbtc08.dll. Install PicoSDK/PicoLog, add the DLL directory "
        "to PATH, copy the PicoSDK DLLs next to this app, or pass "
        "dll_path='C:/path/to/usbtc08.dll'. "
        f"Last load error: {last_error}"
    )


class Tc08Instrument:
    def __init__(self, dll_path: Optional[str] = None, reject_60hz: bool = False):
        """
        reject_60hz:
            False -> reject 50 Hz mains, suitable for Sweden/Europe.
            True  -> reject 60 Hz mains.
        """
        self.dll_path = dll_path
        self.reject_60hz = reject_60hz
        self.dll: Optional[ctypes.WinDLL] = None
        self.handle: Optional[int] = None
        self.enabled_channels: dict[int, str] = {}

    def open(self) -> None:
        if self.handle is not None:
            return

        self.dll = _load_tc08_dll(self.dll_path)
        self._bind_functions()

        handle = int(self.dll.usb_tc08_open_unit())
        if handle <= 0:
            # handle == 0 means no unit found, -1 means failed to open.
            err = self.get_last_error(handle=0)
            if handle == 0:
                raise Tc08Error("No USB TC-08 unit found. Is it connected and not used by PicoLog?")
            raise Tc08Error(f"Failed to open USB TC-08. Error {err}: {self.error_name(err)}")

        self.handle = handle

        ok = int(self.dll.usb_tc08_set_mains(self._handle(), 1 if self.reject_60hz else 0))
        if ok != 1:
            self._raise_last_error("Failed to set mains rejection")

    def close(self) -> None:
        if self.dll is not None and self.handle is not None:
            try:
                self.dll.usb_tc08_stop(self._handle())
            except Exception:
                pass
            try:
                self.dll.usb_tc08_close_unit(self._handle())
            finally:
                self.handle = None
                self.enabled_channels.clear()

    def __enter__(self) -> "Tc08Instrument":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def identify(self) -> str:
        """Return formatted device info, including driver, hardware, serial and calibration date."""
        assert self.dll is not None
        buf = ctypes.create_string_buffer(256)
        ok = int(self.dll.usb_tc08_get_formatted_info(self._handle(), buf, len(buf)))
        if ok not in (0, 1):
            self._raise_last_error("Failed to read unit info")
        return buf.value.decode("ascii", errors="replace").strip()

    def configure_channel(self, channel: int, tc_type: str = "K") -> None:
        """
        Enable or disable a channel.

        channel:
            0 = cold junction
            1..8 = thermocouple channels

        tc_type:
            B, E, J, K, N, R, S, T for thermocouples
            X for voltage input
            " " to disable
        """
        assert self.dll is not None

        if channel not in VALID_CHANNELS:
            raise ValueError("TC-08 channel must be 0..8, where 0 is cold junction and 1..8 are inputs.")

        tc_type = tc_type.upper() if tc_type != " " else " "
        if tc_type not in VALID_TC_TYPES:
            raise ValueError(f"Unsupported TC-08 channel type {tc_type!r}. Use one of {sorted(VALID_TC_TYPES)!r}.")

        ok = int(self.dll.usb_tc08_set_channel(self._handle(), int(channel), tc_type.encode("ascii")))
        if ok != 1:
            self._raise_last_error(f"Failed to configure TC-08 channel {channel}")

        if tc_type == " ":
            self.enabled_channels.pop(channel, None)
        else:
            self.enabled_channels[channel] = tc_type

    def configure_channels(self, channels: dict[int, str]) -> None:
        for channel, tc_type in channels.items():
            self.configure_channel(channel, tc_type)

    def disable_channel(self, channel: int) -> None:
        self.configure_channel(channel, " ")

    def minimum_interval_ms(self) -> int:
        """Approximate minimum conversion interval for the currently enabled channels."""
        assert self.dll is not None
        interval = int(self.dll.usb_tc08_get_minimum_interval_ms(self._handle()))
        if interval <= 0:
            self._raise_last_error("Failed to get minimum interval")
        return interval

    def read_temperatures(self, units: int = TC08_UNITS_CENTIGRADE) -> dict[int, float]:
        """
        Read all currently configured channels on demand.

        Returns:
            dict mapping channel number to temperature.
            Disabled channels are omitted.
        """
        assert self.dll is not None

        temp_array = (ctypes.c_float * 9)()
        overflow_flags = ctypes.c_int16(0)

        ok = int(self.dll.usb_tc08_get_single(
            self._handle(),
            temp_array,
            ctypes.byref(overflow_flags),
            int(units),
        ))

        if ok != 1:
            self._raise_last_error("Failed to read TC-08 temperatures")

        result: dict[int, float] = {}
        for channel in self.enabled_channels:
            value = float(temp_array[channel])
            if not math.isnan(value):
                result[channel] = value

        return result

    def read_temperature(self, channel: int, units: int = TC08_UNITS_CENTIGRADE) -> float:
        readings = self.read_temperatures(units=units)
        if channel not in readings:
            raise Tc08Error(f"No valid reading for TC-08 channel {channel}. Is the channel enabled and connected?")
        return readings[channel]

    def get_cold_junction_temperature(self, units: int = TC08_UNITS_CENTIGRADE) -> float:
        # Make sure CJC channel is explicitly enabled if no thermocouple channel is active.
        if 0 not in self.enabled_channels:
            self.configure_channel(0, "K")
        return self.read_temperature(0, units=units)

    def get_last_error(self, handle: Optional[int] = None) -> int:
        if self.dll is None:
            return -1
        h = self.handle if handle is None else handle
        if h is None:
            h = 0
        return int(self.dll.usb_tc08_get_last_error(ctypes.c_int16(h)))

    @staticmethod
    def error_name(code: int) -> str:
        return TC08_ERROR_CODES.get(code, f"Unknown error {code}")

    def _handle(self) -> ctypes.c_int16:
        if self.handle is None:
            raise Tc08Error("TC-08 is not open.")
        return ctypes.c_int16(self.handle)

    def _raise_last_error(self, message: str) -> None:
        err = self.get_last_error()
        raise Tc08Error(f"{message}. Error {err}: {self.error_name(err)}")

    def _bind_functions(self) -> None:
        assert self.dll is not None

        self.dll.usb_tc08_open_unit.argtypes = []
        self.dll.usb_tc08_open_unit.restype = ctypes.c_int16

        self.dll.usb_tc08_close_unit.argtypes = [ctypes.c_int16]
        self.dll.usb_tc08_close_unit.restype = ctypes.c_int16

        self.dll.usb_tc08_stop.argtypes = [ctypes.c_int16]
        self.dll.usb_tc08_stop.restype = ctypes.c_int16

        self.dll.usb_tc08_set_mains.argtypes = [ctypes.c_int16, ctypes.c_int16]
        self.dll.usb_tc08_set_mains.restype = ctypes.c_int16

        self.dll.usb_tc08_get_minimum_interval_ms.argtypes = [ctypes.c_int16]
        self.dll.usb_tc08_get_minimum_interval_ms.restype = ctypes.c_int32

        self.dll.usb_tc08_get_formatted_info.argtypes = [
            ctypes.c_int16,
            ctypes.c_char_p,
            ctypes.c_int16,
        ]
        self.dll.usb_tc08_get_formatted_info.restype = ctypes.c_int16

        self.dll.usb_tc08_get_last_error.argtypes = [ctypes.c_int16]
        self.dll.usb_tc08_get_last_error.restype = ctypes.c_int16

        self.dll.usb_tc08_set_channel.argtypes = [
            ctypes.c_int16,
            ctypes.c_int16,
            ctypes.c_char,
        ]
        self.dll.usb_tc08_set_channel.restype = ctypes.c_int16

        self.dll.usb_tc08_get_single.argtypes = [
            ctypes.c_int16,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int16,
        ]
        self.dll.usb_tc08_get_single.restype = ctypes.c_int16


def detect_tc08_units(dll_path: Optional[str] = None) -> list[Tc08ProbeResult]:
    """
    Try to open all connected TC-08 units.

    Note:
        This temporarily opens units and then closes them.
    """
    results: list[Tc08ProbeResult] = []

    while True:
        inst = Tc08Instrument(dll_path=dll_path)
        try:
            inst.open()
            assert inst.handle is not None
            results.append(Tc08ProbeResult(handle=inst.handle, info=inst.identify()))
        except Tc08Error:
            break
        finally:
            inst.close()

    return results


if __name__ == "__main__":
    # Quick manual test.
    with Tc08Instrument(reject_60hz=False) as tc08:
        print(tc08.identify())

        # Example: enable channel 1 as Type K.
        tc08.configure_channel(1, "K")

        print(f"Minimum interval: {tc08.minimum_interval_ms()} ms")
        print(f"Channel 1: {tc08.read_temperature(1):.3f} °C")
