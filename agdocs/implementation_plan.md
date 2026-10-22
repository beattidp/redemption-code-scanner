# Implementation Plan: Manual UVC Focus, Programmatic Best-Focus Search, Test Mode, Clean Preview & Code Editing

Provide programmatic UVC manual focus control with JSON configuration and expanding search, a diagnostic `--test` mode, a borderless off-white code preview, and interactive list editing and deletion.

## User Review Required

> [!IMPORTANT]
> **1. UVC Focus Control & Programmatic Search Findings:**
> - **Hardware AF Evaluated**: Querying `v4l2-ctl` on connected USB Camera `1e45:0209` (`/dev/video2`) confirmed UVC compliance:
>   - `focus_automatic_continuous` defaults to `1` (hardware AF loop active).
>   - When active, `focus_absolute` was defaulting to `68` (sharpness score: **8.2**, severely out of focus / blurry).
>   - Disabling hardware AF (`focus_automatic_continuous = 0`) and setting `focus_absolute = 313` produced a pin-sharp image with sharpness score **3020.2** (over 360x sharper!).
>   - At focus 313, `5D4M7-K9XH-95P6` was recognized immediately on the first frame.
>
> - **Configuration Persistence (`camera_config.json`)**:
>   - Assumed starting focus (`"focus_absolute": 313`) and AF disabled (`"autofocus_disabled": true`) stored in `camera_config.json`.
>
> - **Expanding Binary Focus Search**:
>   - Starts from the configured focus value ($F_0$).
>   - Rapidly samples sharpness (Laplacian variance on the ROI code area) in an expanding binary / hill-climbing search with configurable search window (e.g. $\pm 25$ steps) in $\le 0.3$s.
>   - Locks in peak sharpness and updates `camera_config.json`.
>   - Includes GUI controls: Focus value readout, sharpness meter, `[-]` / `[+]` manual nudge buttons, and "🔍 Optimize Focus" button.
>
> **2. Test Mode (`--test` / `-t` flag):**
>   - Archives the full camera frame (`frame_#<idx>_<timestamp>.png`), the processed OCR crop (`crop_#<idx>_<timestamp>.png`), and metadata (elapsed recognition time, focus level, sharpness, OCR pass) into `test_runs/session_<timestamp>/`.
>   - Automatically writes `captured_codes_test.txt` upon application exit.
>
> **3. Clean Off-White Crop Preview:**
>   - Removes the artificial 20px blank white border (`copyMakeBorder(..., value=255)`).
>   - Displays the tight, borderless off-white code pill in the GUI PiP preview and passes it directly to OCR.
>
> **4. Click-to-Edit & Right-Click-to-Delete:**
>   - **Double-Click or Enter**: Opens an edit dialog to fix any character in place.
>   - **Right-Click Context Menu**: Provides `Edit Code...`, `Delete Code`, and `Copy Code`.
>   - **Delete Key**: Deletes selected entry with immediate disk file synchronization.

---

## Proposed Changes

### Configuration & Hardware Focus Layer

#### [NEW] [camera_config.json](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_config.json)
Initial hardware camera configuration:
```json
{
  "autofocus_disabled": true,
  "focus_absolute": 313,
  "focus_search_range": 30,
  "focus_step": 3
}
```

#### [NEW] [camera_scanner/focus.py](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/focus.py)
- Manages UVC focus controls for `/dev/video*` via `v4l2-ctl` / `ioctl`.
- Methods:
  - `disable_autofocus(device_node) -> bool`
  - `set_focus(device_node, value: int) -> bool`
  - `get_focus(device_node) -> int`
  - `calculate_sharpness(frame, roi) -> float`: Computes Laplacian variance.
  - `optimize_focus(cap, device_node, initial_val, search_range, step) -> int`: Fast expanding binary / hill-climbing search that finds and locks optimal focus.
  - `load_config() / save_config()`: Reads and updates `camera_config.json`.

---

### Storage Layer

#### [MODIFY] [camera_scanner/storage.py](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/storage.py)

- Add `update_code(index: int, new_code: str) -> Tuple[bool, Optional[str]]`: Updates code entry in-place, updates lookup set/map, and re-saves the file.
- Add `delete_code(index: int) -> Tuple[bool, Optional[CodeEntry]]`: Deletes entry, re-indexes remaining entries, and re-saves the file.

---

### OCR Engine

#### [MODIFY] [camera_scanner/ocr_engine.py](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/ocr_engine.py)

- Remove artificial 20px white padding `copyMakeBorder` to deliver tight off-white crop directly to preview and OCR.
- Record execution time (latency in milliseconds) and winning OCR pass name for test mode logging.

---

### GUI & Test Mode

#### [MODIFY] [camera_scanner/gui.py](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/gui.py)

- **Focus Control Integration**:
  - On camera connect, apply `focus_automatic_continuous = 0` and `focus_absolute = 313` from `camera_config.json`.
  - Add Focus control panel in GUI: Focus readout (e.g. `Focus: 313`), `[-]` and `[+]` buttons, and `🔍 Optimize Focus` button.
- **Test Mode (`--test`)**:
  - Accept `test_mode: bool = False`.
  - Show `🧪 [TEST MODE]` badge.
  - On code detection, archive original full camera frame, processed crop, and latency metadata into `test_runs/session_<timestamp>/`.
  - On exit, automatically write `captured_codes_test.txt`.
- **Treeview Editing & Context Menu**:
  - Bind `<Double-Button-1>` and `<Return>` to open an in-place Edit Code modal dialog.
  - Bind `<Button-3>` (right-click) for context menu (`Edit Code...`, `Delete Code`, `Copy Code`).
  - Bind `<Delete>` / `<BackSpace>` to delete selected item.

---

### CLI Entrypoint

#### [MODIFY] [main.py](file:///home/beattidp/Documents/antigravity/excited-euclid/main.py)

- Add `--test` / `-t` argument, passed to `ScannerApp(default_file=args.file, test_mode=args.test)`.

---

## Verification Plan

### Automated Tests
1. Unit tests in `tests/test_storage.py` for `update_code` and `delete_code`.
2. Unit tests in `tests/test_focus.py` for sharpness calculation, config loading/saving, and expanding focus search logic.
3. Unit tests in `tests/test_ocr.py` verifying borderless off-white crops across all packages.
   ```bash
   python3 -m unittest discover tests
   ```

### Hardware & Manual Verification
1. Verify manual focus control:
   - Run `python3 -c "from camera_scanner.focus import FocusController; fc = FocusController(); print(fc.get_focus('/dev/video2'))"`
2. Launch in Test Mode:
   ```bash
   python3 main.py --test
   ```
   - Check that focus initializes to 313 with AF disabled.
   - Verify that the preview displays the tight off-white crop with no white border.
   - Click "🔍 Optimize Focus" and observe the real-time sharpness score.
   - Read a code, verify `test_runs/session_*/` captures the raw frame and crop.
   - Double-click a code to edit, right-click to delete.
   - Exit the app and verify `captured_codes_test.txt` is created with the session codes.
