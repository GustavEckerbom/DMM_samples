# DMM Logger

A PySide6 GUI application for logging measurements from SCPI-compatible digital multimeters over serial ports.

## Quick start (Linux)

1. Clone the repository:

```bash
git clone <repo-url> "DMM Interface"
cd "DMM Interface"
```

2. Create and activate a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

4. Run the application:

```bash
python visamples.py
```

## What it does

- Detects SCPI-compatible DMMs on available serial ports
- Lets you select 1 to 4 DMMs
- Supports DC voltage and DC current measurements
- Configures range and NPLC per meter
- Sets sample period (measurement frequency) in seconds
- Logs live readings to CSV

## Files

- `visamples.py` - GUI entrypoint
- `gui.py` - PySide6 user interface
- `dmm_comm.py` - SCPI serial communication helpers
- `logger_worker.py` - background logging thread
- `requirements.txt` - Python dependencies

## Notes

- Tested on Windows first, designed to be cross-platform
- On Linux, use a virtual environment and make sure your user has access to the serial device nodes (for example, add your user to the `dialout` group)
- If you need help with serial port permissions:

```bash
sudo usermod -a -G dialout $USER
newgrp dialout
```
