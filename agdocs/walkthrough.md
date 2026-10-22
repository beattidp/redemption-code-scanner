# Walkthrough: Alignment Window Edge Extension, Focus Control & Test Mode

Successfully calibrated the alignment window to extend edge-to-edge across the frame with the upper 60% eliminated, ensuring reliable OCR for wider-font packages (such as brown packaging) while isolating the code pill from extraneous text.

---

## What Was Accomplished

### 1. Alignment Window Full-Width & Upper Clutter Elimination
- **Package Variation Context**: Packages exist in two variants:
  - **Gray packaging**: Standard smaller font.
  - **Brown packaging**: Slightly larger/wider font (`PTX57-DF5P-9MJD`).
- **Issues with Previous Alignment Box (`x=0.05, w=0.90, y=0.28, h=0.32`)**:
  - The right edge cut through the final character `'D'` on wider-font brown packages, leading to misreads such as `PTX57-DF5P-9MUJ`.
  - The tall upper 60% region encompassed extraneous cardboard text, arrows, "OPEN HERE", and "ENTER REWARDS CODE / ONREWARDS.CO", which caused noise in contour extraction.
- **Calibrated Alignment Box Parameters**:
  - **Horizontal Span**: Extended edge-to-edge (`x = 0.0`, `w = 1.0`). Prevents truncation of leading `'P'` or trailing `'D'`.
  - **Lower Edge**: Kept strictly as-is at $Y_{\text{bottom}} = 0.60$ ($y + h = 0.472 + 0.128 = 0.600$).
  - **Height Reduction**: Reduced height by 60% (from $0.32$ down to $0.128 \approx 13\%$), eliminating the upper $0.28 \to 0.472$ portion of the frame.
- **Updated Default ROI ([`camera_scanner/ocr_engine.py`](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/ocr_engine.py))**:
  ```python
  @dataclass
  class ROI:
      x: float = 0.0
      y: float = 0.472
      w: float = 1.0
      h: float = 0.128
  ```
- **GUI Updates ([`camera_scanner/gui.py`](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/gui.py))**:
  - Presets updated to edge-to-edge full width with `Center` mapping to `(0.0, 0.472, 1.0, 0.128)`.
  - Coordinate status display formatted to `ROI: Y=47% H=13%`.
  - Video canvas rectangle clamping updated to cleanly render 2px boundary lines along `[0, fw-1]`.

---

### 2. Manual Focus Control & Sharpness Optimization
- **Hardware AF Evaluation**: Discovered that UVC camera 1e45:0209 defaulted on launch to continuous autofocus (`focus_automatic_continuous = 1`) and focus position 68 (blurry, sharpness score **8.2**). During the first ~150ms of camera initialization, blurred frames caused `5D4M7` to be read as `9D4M7`, which was then locked in by stationary debounce guards.
- **Calibrated Optimal Focus**: Swept lens actuator positions (0–1023) and discovered optimal focal peak at **`focus_absolute = 313`** with sharpness score **>1500–3000** (Laplacian variance).
- **Persistent Configuration ([`camera_config.json`](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_config.json))**:
  ```json
  {
    "autofocus_disabled": true,
    "focus_absolute": 313,
    "focus_search_range": 30,
    "focus_step": 3
  }
  ```
- **Focus Controller Module ([`camera_scanner/focus.py`](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/focus.py))**:
  - `apply_configured_focus(dev_path)`: Disables hardware continuous autofocus and locks lens actuator to configured position.
  - `calculate_sharpness(frame)`: Computes Laplacian variance.
  - `optimize_focus(cap, dev_path, ...)`: Performs an expanding search around starting focus, evaluating edge sharpness and locking the peak.
- **GUI Controls & Camera Warmup**:
  - Added Alignment Guide Row 3: Focus value display (`Focus: 313`), `[-] Focus`, `[+] Focus`, `🔍 Optimize Focus`, and live sharpness status.
  - Added a 15-frame warmup delay before OCR activation on launch, preventing startup blur from committing erroneous reads.

---

### 3. Diagnostic Test Mode (`--test` / `-t`)
- **CLI Flag**: Added `--test` / `-t` to [`main.py`](file:///home/beattidp/Documents/antigravity/excited-euclid/main.py).
- **Session Artifacts**: When run with `--test`:
  - GUI displays a purple `🧪 TEST MODE ACTIVE` badge.
  - A timestamped session directory is created in `test_runs/session_<timestamp>/`.
  - For every detected code, the app saves:
    - Original full camera view: `cap_<seq>_<code >_raw.jpg`
    - Deskewed clean off-white crop: `cap_<seq>_<code >_crop.jpg`
    - Timing & diagnostics line appended to `test_log.txt`:
      `[TIMESTAMP] #001 [NEW] Code: PTX57-DF5P-9MJD | Latency: 497.6ms | Pass: Pass 1: Median PSM 6 | Focus: 313`
  - On exit (`_on_close`), writes **`captured_codes_test.txt`** in the project root containing all captured codes.

---

### 4. Clean Off-White Crop Preview (No Artificial White Borders)
- Removed `cv2.copyMakeBorder(..., value=255)` so the raw rotated off-white code pill is passed directly to the PiP preview and OCR engine.
- Reordered tiered OCR passes in [`camera_scanner/ocr_engine.py`](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/ocr_engine.py) to prioritize PSM 6 (uniform block segmentation) over PSM 7. This eliminates character aspect ratio distortions and preserves both `5` and `9` without baseline clipping.

---

### 5. Interactive List Management (Edit, Delete, Copy)
- **Inline Editing**: Double-clicking (`<Double-Button-1>`) or pressing Enter (`<Return>`) on any captured code in the Treeview opens an in-place Edit dialog to correct OCR misreads.
- **Context Menu**: Right-clicking (`<Button-3>` / `<Button-2>`) opens a popup menu with:
  - ✏️ **Edit Code...**
  - 🗑️ **Delete Code**
  - 📋 **Copy Code**
- **Keyboard Shortcuts**: Pressing `<Delete>` or `<BackSpace>` prompts confirmation to delete the selected code.
- **Storage Synchronization ([`camera_scanner/storage.py`](file:///home/beattidp/Documents/antigravity/excited-euclid/camera_scanner/storage.py))**:
  - `update_code(index, new_code)`: Updates in-memory lookup maps and re-saves the active file.
  - `delete_code(index)`: Removes entry, re-indexes remaining entries (1..N), updates lookup sets, and re-saves the file.

---

## Verification Results

### 1. Live Brown Package Verification (`/dev/video2`)
Executed live test on the brown package currently on the fixture:
```
Camera: /dev/video2
ROI: ROI(x=0.0, y=0.472, w=1.0, h=0.128)
Scanned codes: ['PTX57-DF5P-9MJD']
Scan info: {'code': 'PTX57-DF5P-9MJD', 'latency_ms': 497.6, 'pass': 'Pass 1: Median PSM 6'}
Crop shape: (120, 714)
```
- **Accuracy**: Code read with 100% precision as **`PTX57-DF5P-9MJD`** (previously truncated as `9MUJ`).
- **Speed**: **497.6 ms** execution time.
- **Crop Width**: Expanded from 641px to 714px, comfortably including the full `'D'` with margins.

### 2. Automated Test Suite (16/16 Passed)
```bash
$ python3 -m unittest discover tests
................
----------------------------------------------------------------------
Ran 16 tests in 15.219s

OK
```

---

## How to Run

### Standard Production Mode
```bash
./run.sh
# or
python3 main.py
```

### Diagnostic Test Mode
```bash
python3 main.py --test
```
