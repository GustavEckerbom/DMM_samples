# ThermoPi

PySide-compatible GUI application for logging measurements from serial DMMs, a Pico USB TC-08 thermocouple logger, and an optional serial-controlled thermal chamber.

## Quick Start On Raspberry Pi OS

On Raspberry Pi OS 32-bit, install Qt from apt first. PySide6 is usually not available from pip on 32-bit Raspberry Pi OS, and a normal virtual environment cannot see apt-installed PyQt5 unless it is created with `--system-site-packages`.

1. Install system Qt:

```bash
sudo apt update
sudo apt install python3-pyqt5
```

2. Clone the repo and enter it:

```bash
cd ~/Desktop
git clone https://github.com/GustavEckerbom/ThermoPi.git
cd ThermoPi
```

If you already created an extra outer `ThermoPi` folder before cloning, enter the inner repo folder instead, for example `cd ~/Desktop/ThermoPi/ThermoPi`.

3. Create and activate a virtual environment that can see system PyQt5:

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
```

4. Install Python package dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

5. Run the application:

```bash
python visamples.py
```

## Quick Start On Windows Or x86_64 Linux

1. Clone the repo and enter it:

```bash
git clone https://github.com/GustavEckerbom/ThermoPi.git
cd ThermoPi
```

2. Create and activate a Python virtual environment:

```bash
python -m venv venv
venv\Scripts\activate
```

On Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies and Qt bindings:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install PySide6
```

4. Run the application:

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
