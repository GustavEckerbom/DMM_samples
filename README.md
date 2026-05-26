# ThermoPi

PySide-compatible GUI application for logging measurements from serial DMMs, a Pico USB TC-08 thermocouple logger, and an optional serial-controlled thermal chamber.

## Quick Start

1. Create and activate a Python virtual environment:

```bash
python -m venv venv
venv\Scripts\activate
```

On Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

3. Run the application:

```bash
python visamples.py
```

## What It Does

- Detects SCPI-compatible DMMs on serial ports.
- Lets you log 0 to 4 DMMs with DC voltage or DC current configuration.
- Supports Pico USB TC-08 thermocouple channels 1 to 8.
- Provides an optional thermal chamber temperature program.
- Plots live DMM, TC-08, and chamber data.
- Writes measurements and run metadata to CSV.

## Thermal Chamber Program

The Thermal Chamber tab is optional. When enabled:

- The GUI detects chamber ports by sending the `$00I` chamber query and only lists ports that respond like a chamber.
- Each program row contains a target `Temperature C` and a `Hold min` duration.
- The chamber is commanded to the first target when logging starts.
- The intended curve ramps using fixed chamber rates:
  - heating: `1.8 K/min`
  - cooling: `1.5 K/min`
- Hold time starts after the intended temperature reaches the target.
- The last target temperature is held after the final duration until logging is stopped.

CSV rows include chamber current temperature, commanded set temperature, and ramp-limited intended temperature when the chamber program is enabled.

## Files

- `visamples.py` - GUI entrypoint.
- `gui.py` - Main Qt GUI, instrument configuration tabs, and live plots.
- `logger_worker.py` - Background logging thread, CSV writer, chamber profile timing.
- `dmm_comm.py` - SCPI DMM serial communication helpers.
- `pico_tc08_comm.py` - Pico USB TC-08 wrapper around the Pico driver library.
- `thermo_comm.py` - Thermal chamber serial communication helpers and port probing.
- `qt_compat.py` - Qt import compatibility layer for PySide6, PySide2, and PyQt5.
- `requirements.txt` - Python dependencies.

## Hardware Notes

- DMMs are detected with SCPI `*IDN?`.
- TC-08 support needs the Pico driver library available on the system or next to the app.
- Thermal chamber support uses the existing serial protocol commands in `thermo_comm.py`.
- On Linux, make sure your user can access serial device nodes, for example by joining the `dialout` group:

```bash
sudo usermod -a -G dialout $USER
newgrp dialout
```
