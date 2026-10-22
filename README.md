# Package Redemption Code Scanner (USB Camera 1e45:0209)

A desktop application designed for Linux to automatically capture redemption codes from packages placed in the camera view using a connected USB camera (device ID `1e45:0209`).

## Features

- **Automatic Device Discovery**: Dynamically matches and connects to USB camera `1e45:0209` (`/dev/video2`) via sysfs, with automatic fallback and device selection dropdown.
- **High-Accuracy OCR**: Calibrated for redemption codes (e.g. `GST8S-JLPZ-KKXK`) using Tesseract with real-time ROI cropping and pre-filtering.
- **Live Alignment Guide**: Displays a targeted visual bounding box with corner guides directly on the video stream to assist repeatable package placement.
- **Auto-Scrolling Code List**: Displays captured codes in a clean list with sequence index, timestamps, and auto-scrolls to the newest item as codes arrive.
- **Duplicate Detection & Notification**: Detects previously scanned codes immediately and displays a non-blocking amber warning banner (`⚠️ DUPLICATE DETECTED`) without interrupting scanning.
- **Persistent Text File Storage**: Automatically appends new codes to `captured_codes.txt` (or custom file). Re-opening or restarting the app automatically reloads previous codes and resumes scanning without duplicates.
- **Controls & Persistence Options**:
  - Load / Switch active file
  - Save As / Export
  - Pause / Resume scanning
  - Guide box alignment presets (Center, Upper, Wide, Full Frame)
  - Manual capture button
  - Copy code to clipboard

## Quick Start

### 1. Launch the App
Ensure your camera `1e45:0209` is plugged in, then run:

```bash
./run.sh
```

Or using python:

```bash
python3 main.py
```

To specify a custom codes file on launch:

```bash
python3 main.py --file /path/to/my_codes.txt
```

### 2. Scanning Packages
1. Place a package in the field of view so the code falls inside the green guide box.
2. The scanner will instantly detect the code, emit an audio beep (if enabled), add it to the list, and display a green success banner (`✅ CAPTURED: <CODE>`).
3. If an identical package or already-captured code is presented, the app will show an amber duplicate banner (`⚠️ DUPLICATE DETECTED: <CODE> (Already captured at #...)`).
4. Captured codes are immediately persisted to disk.

## Testing

Run the automated test suite:

```bash
python3 -m unittest discover tests
```
---

### Credit where Credit is Due:
The code for this application was developed with [Google Antigravity](http://antigravity.google/).  _Thank you!_

