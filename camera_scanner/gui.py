"""Desktop GUI application for scanning redemption codes using USB Camera 1e45:0209."""

import os
import sys
import time
import queue
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from typing import Optional, Tuple, List

import cv2
import numpy as np
from PIL import Image, ImageTk

from camera_scanner.device import find_target_camera, list_available_cameras
from camera_scanner.storage import CodeStorage, CodeEntry
from camera_scanner.ocr_engine import OCREngine, ROI, DEFAULT_PATTERN
from camera_scanner.focus import FocusController

logger = logging.getLogger(__name__)


class ScannerApp(tk.Tk):
    def __init__(self, default_file: str = "captured_codes.txt", test_mode: bool = False):
        super().__init__()
        self.test_mode = test_mode
        title_suffix = " 🧪 [TEST MODE]" if self.test_mode else ""
        self.title(f"Redemption Code Scanner (USB Camera 1e45:0209){title_suffix}")
        self.geometry("1220x840")
        self.minsize(980, 680)

        # Storage, Focus Controller, and OCR
        self.storage = CodeStorage(default_filepath=default_file)
        self.focus_controller = FocusController()
        self.ocr_engine = OCREngine()
        self.duplicate_count = 0
        self.session_new_count = 0
        self.rotation_mode = "90_CCW"  # Default 90 CCW as requested by fixture modification

        # Camera & Worker Threading State
        self.cap: Optional[cv2.VideoCapture] = None
        self.camera_index: Optional[int] = None
        self.camera_device_path: Optional[str] = None
        self.camera_running = False
        self.scanning_enabled = True
        self.camera_warmup_frames = 0

        # Test Mode session storage
        self.test_session_dir: Optional[str] = None
        self.test_log_file: Optional[str] = None
        self.test_captured_codes: List[str] = []
        self.test_capture_counter = 0

        if self.test_mode:
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.test_session_dir = os.path.join(os.getcwd(), "test_runs", f"session_{ts_str}")
            os.makedirs(self.test_session_dir, exist_ok=True)
            self.test_log_file = os.path.join(self.test_session_dir, "test_log.txt")
            with open(self.test_log_file, "w", encoding="utf-8") as f:
                f.write(f"=== TEST RUN STARTED AT {datetime.now()} ===\n")
            logger.info(f"Test Mode active: logging to {self.test_session_dir}")

        self.latest_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self.event_queue: queue.Queue = queue.Queue()

        self.worker_thread: Optional[threading.Thread] = None
        self.ocr_thread: Optional[threading.Thread] = None
        self.stop_threads = threading.Event()

        # Banner reset timer tracking
        self.banner_reset_timer: Optional[str] = None

        # Build UI layout
        self._setup_styles()
        self._build_ui()

        # Load existing codes on startup
        self._load_initial_codes()

        # Initialize Camera
        self._init_camera()

        # Start periodic GUI queue listener and video updater
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_events()
        self._update_video_canvas()

    def _setup_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Header.TLabel", font=("Helvetica", 11, "bold"))
        style.configure("Stat.TLabel", font=("Helvetica", 10))
        style.configure("Treeview.Heading", font=("Helvetica", 10, "bold"))
        style.configure("Treeview", font=("Courier", 10), rowheight=24)

    def _build_ui(self):
        # Main container with two columns
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Top Bar: Camera info, status, and device picker
        top_bar = ttk.Frame(main_frame)
        top_bar.pack(fill=tk.X, pady=(0, 10))

        self.cam_status_var = tk.StringVar(value="Searching for Camera 1e45:0209...")
        self.cam_status_lbl = tk.Label(
            top_bar,
            textvariable=self.cam_status_var,
            font=("Helvetica", 10, "bold"),
            bg="#374151",
            fg="#f9fafb",
            padx=10,
            pady=4,
            relief=tk.FLAT
        )
        self.cam_status_lbl.pack(side=tk.LEFT)

        if self.test_mode:
            test_badge = tk.Label(
                top_bar,
                text="🧪 TEST MODE ACTIVE",
                font=("Helvetica", 9, "bold"),
                bg="#7c3aed",
                fg="#ffffff",
                padx=8,
                pady=4,
                relief=tk.FLAT
            )
            test_badge.pack(side=tk.LEFT, padx=8)

        # Camera selection dropdown
        self.cam_combo_var = tk.StringVar()
        self.cam_combo = ttk.Combobox(
            top_bar,
            textvariable=self.cam_combo_var,
            state="readonly",
            width=32
        )
        self.cam_combo.pack(side=tk.LEFT, padx=10)
        self.cam_combo.bind("<<ComboboxSelected>>", self._on_camera_selected)

        ttk.Button(top_bar, text="🔄 Refresh Devices", command=self._refresh_device_list).pack(side=tk.LEFT)

        # Orientation / Rotation selector
        ttk.Label(top_bar, text="Orientation:").pack(side=tk.LEFT, padx=(12, 2))
        self.rot_combo_var = tk.StringVar(value="90° CCW")
        self.rot_combo = ttk.Combobox(
            top_bar,
            textvariable=self.rot_combo_var,
            state="readonly",
            values=["90° CCW", "0° (None)", "180°", "90° CW"],
            width=10
        )
        self.rot_combo.pack(side=tk.LEFT, padx=2)
        self.rot_combo.bind("<<ComboboxSelected>>", self._on_rotation_selected)

        # Audio alerts toggle
        self.sound_enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top_bar, text="🔔 Beep on Scan", variable=self.sound_enabled_var).pack(side=tk.RIGHT, padx=5)

        # Scan active toggle
        self.scan_toggle_btn = ttk.Button(top_bar, text="⏸ Pause Scanner", command=self._toggle_scanning)
        self.scan_toggle_btn.pack(side=tk.RIGHT, padx=5)

        # Content paned layout
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True)
        content_frame.columnconfigure(0, weight=6)  # Camera side
        content_frame.columnconfigure(1, weight=5)  # List side
        content_frame.rowconfigure(0, weight=1)

        # Left Column: Video View & Alignment Guide
        left_col = ttk.LabelFrame(content_frame, text=" Live Camera Feed & Code Alignment Guide ", padding="8")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # Video Canvas
        self.canvas = tk.Canvas(left_col, bg="#111827", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Guide controls below canvas
        guide_ctrl = ttk.Frame(left_col, padding="4")
        guide_ctrl.pack(fill=tk.X, pady=(6, 0))

        # Row 1: Presets for 90 CCW Portrait Frame (Full-width edge-to-edge)
        row1 = ttk.Frame(guide_ctrl)
        row1.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(row1, text="Presets:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row1, text="Center", command=lambda: self._set_roi_preset(0.0, 0.472, 1.0, 0.128)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="Upper", command=lambda: self._set_roi_preset(0.0, 0.32, 1.0, 0.15)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="Lower", command=lambda: self._set_roi_preset(0.0, 0.48, 1.0, 0.15)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="Wide", command=lambda: self._set_roi_preset(0.0, 0.38, 1.0, 0.24)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="Full Frame", command=lambda: self._set_roi_preset(0.0, 0.0, 1.0, 1.0)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="📸 Capture Now", command=self._manual_scan_trigger).pack(side=tk.RIGHT, padx=2)

        # Row 2: Fine-Tuning controls & live coordinate status
        row2 = ttk.Frame(guide_ctrl)
        row2.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(row2, text="Fine-Tune:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row2, text="▲ Up", width=5, command=lambda: self._nudge_roi_y(-0.02)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row2, text="▼ Down", width=6, command=lambda: self._nudge_roi_y(0.02)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row2, text="+ Height", width=8, command=lambda: self._nudge_roi_h(0.02)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row2, text="- Height", width=8, command=lambda: self._nudge_roi_h(-0.02)).pack(side=tk.LEFT, padx=1)

        self.roi_status_var = tk.StringVar(value="ROI: Y=47% H=13%")
        ttk.Label(row2, textvariable=self.roi_status_var, font=("Helvetica", 9, "bold")).pack(side=tk.RIGHT, padx=4)

        # Row 3: Manual Focus & Sharpness Optimization
        row3 = ttk.Frame(guide_ctrl)
        row3.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(row3, text="Focus:").pack(side=tk.LEFT, padx=(0, 4))
        init_f = self.focus_controller.config.get("focus_absolute", 313)
        self.focus_val_lbl = ttk.Label(row3, text=str(init_f), font=("Helvetica", 9, "bold"))
        self.focus_val_lbl.pack(side=tk.LEFT, padx=2)
        ttk.Button(row3, text="- Focus", width=7, command=lambda: self._nudge_focus(-3)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row3, text="+ Focus", width=7, command=lambda: self._nudge_focus(3)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row3, text="🔍 Optimize Focus", command=self._trigger_optimize_focus).pack(side=tk.LEFT, padx=4)
        self.sharpness_status_var = tk.StringVar(value="")
        ttk.Label(row3, textvariable=self.sharpness_status_var, font=("Helvetica", 8)).pack(side=tk.RIGHT, padx=4)

        # Right Column: Notifications, List Pane, Persistence Toolbar
        right_col = ttk.LabelFrame(content_frame, text=" Captured Redemption Codes ", padding="8")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # Notification Banner
        self.banner_frame = tk.Frame(right_col, bg="#2563eb", padx=10, pady=8)
        self.banner_frame.pack(fill=tk.X, pady=(0, 8))

        self.banner_title_var = tk.StringVar(value="Ready for Packages")
        self.banner_detail_var = tk.StringVar(value="Place package inside camera guide box to scan code.")

        self.banner_title_lbl = tk.Label(
            self.banner_frame,
            textvariable=self.banner_title_var,
            font=("Helvetica", 11, "bold"),
            bg="#2563eb",
            fg="#ffffff"
        )
        self.banner_title_lbl.pack(anchor="w")

        self.banner_detail_lbl = tk.Label(
            self.banner_frame,
            textvariable=self.banner_detail_var,
            font=("Helvetica", 9),
            bg="#2563eb",
            fg="#ffffff"
        )
        self.banner_detail_lbl.pack(anchor="w")

        # Stats Bar
        stats_frame = ttk.Frame(right_col)
        stats_frame.pack(fill=tk.X, pady=(0, 6))

        self.stat_total_var = tk.StringVar(value="Total: 0")
        self.stat_session_var = tk.StringVar(value="Session New: 0")
        self.stat_dup_var = tk.StringVar(value="Duplicates: 0")

        ttk.Label(stats_frame, textvariable=self.stat_total_var, style="Header.TLabel").pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(stats_frame, textvariable=self.stat_session_var, style="Stat.TLabel").pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(stats_frame, textvariable=self.stat_dup_var, style="Stat.TLabel").pack(side=tk.LEFT)

        # Treeview List with Scrollbar
        list_container = ttk.Frame(right_col)
        list_container.pack(fill=tk.BOTH, expand=True)

        columns = ("index", "code", "timestamp", "source")
        self.tree = ttk.Treeview(list_container, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("index", text="#")
        self.tree.heading("code", text="Redemption Code")
        self.tree.heading("timestamp", text="Captured Time")
        self.tree.heading("source", text="Source")

        self.tree.column("index", width=42, anchor="center")
        self.tree.column("code", width=180, anchor="w")
        self.tree.column("timestamp", width=140, anchor="center")
        self.tree.column("source", width=65, anchor="center")

        tree_scroll = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Treeview event bindings for inline editing and context menu
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)
        self.tree.bind("<Return>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Button-2>", self._on_tree_right_click)
        self.tree.bind("<Delete>", self._on_tree_delete_key)
        self.tree.bind("<BackSpace>", self._on_tree_delete_key)

        # Context Menu for right click
        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="✏️ Edit Code...", command=self._edit_selected_code)
        self.tree_menu.add_command(label="🗑️ Delete Code", command=self._delete_selected_code)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="📋 Copy Code", command=self._copy_selected_code)

        # Storage File Info & Persistence Toolbar
        storage_frame = ttk.LabelFrame(right_col, text=" File Persistence ", padding="6")
        storage_frame.pack(fill=tk.X, pady=(8, 0))

        self.filepath_var = tk.StringVar(value=f"File: {self.storage.filepath}")
        file_lbl = ttk.Label(storage_frame, textvariable=self.filepath_var, font=("Helvetica", 8))
        file_lbl.pack(fill=tk.X, pady=(0, 4))

        file_btn_frame = ttk.Frame(storage_frame)
        file_btn_frame.pack(fill=tk.X)

        ttk.Button(file_btn_frame, text="📂 Load / Switch File", command=self._choose_and_load_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(file_btn_frame, text="💾 Save As...", command=self._save_as_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(file_btn_frame, text="📋 Copy Code", command=self._copy_selected_code).pack(side=tk.LEFT, padx=2)
        ttk.Button(file_btn_frame, text="🗑️ Clear", command=self._clear_all_codes).pack(side=tk.RIGHT, padx=2)

    def _on_rotation_selected(self, event=None):
        val = self.rot_combo_var.get()
        if "90° CCW" in val:
            self.rotation_mode = "90_CCW"
        elif "90° CW" in val:
            self.rotation_mode = "90_CW"
        elif "180" in val:
            self.rotation_mode = "180"
        else:
            self.rotation_mode = "NONE"

    def _apply_rotation(self, frame: np.ndarray) -> np.ndarray:
        if self.rotation_mode == "90_CCW":
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif self.rotation_mode == "90_CW":
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self.rotation_mode == "180":
            return cv2.rotate(frame, cv2.ROTATE_180)
        return frame

    def _update_roi_label(self):
        roi = self.ocr_engine.roi
        if not self.ocr_engine.use_roi:
            self.roi_status_var.set("ROI: Full Frame")
        else:
            self.roi_status_var.set(f"ROI: Y={int(round(roi.y * 100))}% H={int(round(roi.h * 100))}%")

    def _set_roi_preset(self, x: float, y: float, w: float, h: float):
        self.ocr_engine.roi = ROI(x=x, y=y, w=w, h=h)
        self.ocr_engine.use_roi = (w < 1.0 or h < 1.0)
        self._update_roi_label()

    def _nudge_roi_y(self, delta: float):
        roi = self.ocr_engine.roi
        new_y = max(0.0, min(1.0 - roi.h, round(roi.y + delta, 3)))
        self._set_roi_preset(roi.x, new_y, roi.w, roi.h)

    def _nudge_roi_h(self, delta: float):
        roi = self.ocr_engine.roi
        new_h = max(0.08, min(1.0 - roi.y, round(roi.h + delta, 3)))
        self._set_roi_preset(roi.x, roi.y, roi.w, new_h)

    def _toggle_scanning(self):
        self.scanning_enabled = not self.scanning_enabled
        if self.scanning_enabled:
            self.scan_toggle_btn.configure(text="⏸ Pause Scanner")
            self._show_banner_info("Scanner Active", "Place package in camera view.")
        else:
            self.scan_toggle_btn.configure(text="▶ Resume Scanner")
            self._show_banner_info("Scanner Paused", "Automatic OCR recognition is paused.")

    def _load_initial_codes(self):
        count = self.storage.load_from_file()
        for entry in self.storage.get_all():
            self._insert_entry_to_tree(entry, scroll=False)
        self._update_stats()
        if count > 0:
            self._show_banner_info(
                "Loaded Existing Codes",
                f"Resumed session with {count} codes loaded from {os.path.basename(self.storage.filepath)}."
            )

    def _refresh_device_list(self):
        devices = list_available_cameras()
        options = []
        selected_opt = None
        for d in devices:
            label = f"{d['device_node']} ({d['vendor_id']}:{d['product_id']}) {d['name']}"
            options.append(label)
            if d['vendor_id'] == '1e45' and d['product_id'] == '0209':
                selected_opt = label

        self.cam_combo['values'] = options
        if selected_opt:
            self.cam_combo.set(selected_opt)
        elif options:
            self.cam_combo.set(options[0])

    def _init_camera(self):
        self._refresh_device_list()
        idx, dev_path = find_target_camera()
        if idx is not None:
            self._open_camera(idx, dev_path)
        else:
            self.cam_status_var.set("❌ Camera 1e45:0209 Not Found")
            self.cam_status_lbl.configure(bg="#dc2626")
            self._show_banner_duplicate(
                "Camera Not Found",
                "USB Camera 1e45:0209 was not detected. Please connect it and click Refresh Devices."
            )

    def _open_camera(self, index: int, dev_path: str):
        self._stop_camera()
        self.camera_index = index
        self.camera_device_path = dev_path

        # Apply configured autofocus state and manual focus
        self.focus_controller.apply_configured_focus(dev_path)
        self.camera_warmup_frames = 0
        curr_f = self.focus_controller.config.get("focus_absolute", 313)
        self.focus_val_lbl.configure(text=str(curr_f))

        cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if not cap.isOpened():
            self.cam_status_var.set(f"❌ Failed to open {dev_path}")
            self.cam_status_lbl.configure(bg="#dc2626")
            return

        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        self.cap = cap
        self.camera_running = True
        self.stop_threads.clear()

        self.cam_status_var.set(f"● Connected: 1e45:0209 ({dev_path})")
        self.cam_status_lbl.configure(bg="#15803d")

        self.worker_thread = threading.Thread(target=self._capture_worker, daemon=True)
        self.worker_thread.start()

        self.ocr_thread = threading.Thread(target=self._ocr_worker, daemon=True)
        self.ocr_thread.start()

    def _stop_camera(self):
        self.stop_threads.set()
        self.camera_running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=0.8)
        if self.ocr_thread and self.ocr_thread.is_alive():
            self.ocr_thread.join(timeout=0.8)
        if self.cap:
            self.cap.release()
            self.cap = None

    def _on_camera_selected(self, event=None):
        val = self.cam_combo_var.get()
        if not val:
            return
        node = val.split()[0]
        try:
            idx = int(node.replace("/dev/video", ""))
            self._open_camera(idx, node)
        except ValueError:
            pass

    def _capture_worker(self):
        sharpness_check_counter = 0
        while not self.stop_threads.is_set():
            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    self.camera_warmup_frames += 1
                    frame = self._apply_rotation(frame)
                    with self.frame_lock:
                        self.latest_frame = frame

                    sharpness_check_counter += 1
                    if sharpness_check_counter >= 15:
                        sharpness_check_counter = 0
                        score = self.focus_controller.calculate_sharpness(frame)
                        self.event_queue.put(("SHARPNESS_UPDATE", score))
                else:
                    time.sleep(0.01)
            else:
                time.sleep(0.05)
            time.sleep(0.02)

    def _ocr_worker(self):
        while not self.stop_threads.is_set():
            if not self.scanning_enabled or self.camera_warmup_frames < 15:
                time.sleep(0.05)
                continue

            frame_to_scan = None
            with self.frame_lock:
                if self.latest_frame is not None:
                    frame_to_scan = self.latest_frame.copy()

            if frame_to_scan is not None:
                def on_new(code: str):
                    self.event_queue.put(("NEW_CODE", (code, frame_to_scan)))

                def on_dup(code: str):
                    self.event_queue.put(("DUPLICATE_CODE", (code, frame_to_scan)))

                def is_known(code: str) -> bool:
                    return self.storage.is_duplicate(code)

                try:
                    self.ocr_engine.process_frame(
                        frame_to_scan,
                        on_new_code=on_new,
                        on_duplicate=on_dup,
                        is_known_callback=is_known
                    )
                except Exception as e:
                    logger.error(f"Error in OCR worker: {e}")

            time.sleep(0.25)

    def _poll_events(self):
        try:
            while True:
                ev_type, payload = self.event_queue.get_nowait()
                if ev_type == "NEW_CODE":
                    if isinstance(payload, tuple):
                        code, frame = payload
                    else:
                        code, frame = payload, None
                    self._handle_new_code(code, frame)
                elif ev_type == "DUPLICATE_CODE":
                    if isinstance(payload, tuple):
                        code, frame = payload
                    else:
                        code, frame = payload, None
                    self._handle_duplicate_code(code, frame)
                elif ev_type == "SHARPNESS_UPDATE":
                    self.sharpness_status_var.set(f"Sharpness: {payload:.1f}")
                elif ev_type == "FOCUS_PROGRESS":
                    f_val, score = payload
                    self.focus_val_lbl.configure(text=str(f_val))
                    self.sharpness_status_var.set(f"Sharpness: {score:.1f}")
                elif ev_type == "FOCUS_DONE":
                    best_f = payload
                    self.focus_val_lbl.configure(text=str(best_f))
                    self._show_banner_success("Focus Locked", f"Optimal focus locked at {best_f}.")
                elif ev_type == "BANNER_INFO":
                    title, detail = payload
                    self._show_banner_info(title, detail)
        except queue.Empty:
            pass
        finally:
            self.after(40, self._poll_events)

    def _handle_new_code(self, code: str, frame: Optional[np.ndarray] = None):
        is_new, entry, orig = self.storage.add_code(code)
        if is_new:
            self.session_new_count += 1
            self._insert_entry_to_tree(entry, scroll=True)
            self._update_stats()
            self._show_banner_success(
                f"CAPTURED: {entry.code}",
                f"Added code #{entry.index} at {entry.timestamp}. Automatically saved."
            )
            if self.test_mode:
                self._log_test_capture(entry.code, frame, status="NEW")
            if self.sound_enabled_var.get():
                self.bell()
        else:
            self._handle_duplicate_code(code, frame)

    def _handle_duplicate_code(self, code: str, frame: Optional[np.ndarray] = None):
        self.duplicate_count += 1
        self._update_stats()
        is_dup, _, orig = self.storage.add_code(code)
        orig_info = f" (Captured at #{orig.index} on {orig.timestamp})" if orig else ""
        self._show_banner_duplicate(
            f"DUPLICATE DETECTED: {code}",
            f"This code is already captured{orig_info}."
        )
        if self.test_mode:
            self._log_test_capture(code, frame, status="DUPLICATE")
        if self.sound_enabled_var.get():
            self.bell()

    def _insert_entry_to_tree(self, entry: CodeEntry, scroll: bool = True):
        item_id = self.tree.insert(
            "",
            "end",
            values=(entry.index, entry.code, entry.timestamp, entry.source)
        )
        if scroll:
            self.tree.see(item_id)
            self.tree.selection_set(item_id)

    def _update_stats(self):
        total = self.storage.count()
        self.stat_total_var.set(f"Total: {total}")
        self.stat_session_var.set(f"Session New: {self.session_new_count}")
        self.stat_dup_var.set(f"Duplicates: {self.duplicate_count}")

    def _cancel_banner_timer(self):
        if self.banner_reset_timer:
            self.after_cancel(self.banner_reset_timer)
            self.banner_reset_timer = None

    def _show_banner_success(self, title: str, detail: str):
        self._cancel_banner_timer()
        self.banner_frame.configure(bg="#15803d")
        self.banner_title_lbl.configure(bg="#15803d", fg="#ffffff")
        self.banner_detail_lbl.configure(bg="#15803d", fg="#dcfce7")
        self.banner_title_var.set(f"✅ {title}")
        self.banner_detail_var.set(detail)
        self.banner_reset_timer = self.after(2500, self._reset_banner_to_idle)

    def _show_banner_duplicate(self, title: str, detail: str):
        self._cancel_banner_timer()
        self.banner_frame.configure(bg="#d97706")
        self.banner_title_lbl.configure(bg="#d97706", fg="#ffffff")
        self.banner_detail_lbl.configure(bg="#d97706", fg="#fef3c7")
        self.banner_title_var.set(f"⚠️ {title}")
        self.banner_detail_var.set(detail)
        self.banner_reset_timer = self.after(2500, self._reset_banner_to_idle)

    def _show_banner_info(self, title: str, detail: str):
        self._cancel_banner_timer()
        self.banner_frame.configure(bg="#2563eb")
        self.banner_title_lbl.configure(bg="#2563eb", fg="#ffffff")
        self.banner_detail_lbl.configure(bg="#2563eb", fg="#eff6ff")
        self.banner_title_var.set(f"ℹ️ {title}")
        self.banner_detail_var.set(detail)

    def _reset_banner_to_idle(self):
        self.banner_reset_timer = None
        self.banner_frame.configure(bg="#1f2937")
        self.banner_title_lbl.configure(bg="#1f2937", fg="#f3f4f6")
        self.banner_detail_lbl.configure(bg="#1f2937", fg="#9ca3af")
        self.banner_title_var.set("Ready for Next Package")
        self.banner_detail_var.set("Align code in field of view. Duplicates will be alerted.")

    def _update_video_canvas(self):
        frame = None
        with self.frame_lock:
            if self.latest_frame is not None:
                frame = self.latest_frame.copy()

        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        if frame is not None and canvas_w > 10 and canvas_h > 10:
            fh, fw = frame.shape[:2]

            if self.ocr_engine.use_roi:
                rx, ry, rw, rh = self.ocr_engine.roi.get_pixel_box(fw, fh)
                bx1 = max(0, min(rx, fw - 1))
                by1 = max(0, min(ry, fh - 1))
                bx2 = max(bx1 + 10, min(rx + rw - 1, fw - 1))
                by2 = max(by1 + 10, min(ry + rh - 1, fh - 1))
                cv2.rectangle(frame, (bx1, by1), (bx2, by2), (0, 255, 128), 2)
                c_len = min(25, (bx2 - bx1) // 5, (by2 - by1) // 5)
                color = (0, 255, 255)
                cv2.line(frame, (bx1, by1), (bx1 + c_len, by1), color, 3)
                cv2.line(frame, (bx1, by1), (bx1, by1 + c_len), color, 3)
                cv2.line(frame, (bx2, by1), (bx2 - c_len, by1), color, 3)
                cv2.line(frame, (bx2, by1), (bx2, by1 + c_len), color, 3)
                cv2.line(frame, (bx1, by2), (bx1 + c_len, by2), color, 3)
                cv2.line(frame, (bx1, by2), (bx1, by2 - c_len), color, 3)
                cv2.line(frame, (bx2, by2), (bx2 - c_len, by2), color, 3)
                cv2.line(frame, (bx2, by2), (bx2, by2 - c_len), color, 3)

                label_text = "ALIGN CODE HERE" if self.scanning_enabled else "SCANNER PAUSED"
                cv2.putText(frame, label_text, (bx1 + 8, max(24, by1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 128), 2, cv2.LINE_AA)

            scale = min(canvas_w / fw, canvas_h / fh)
            disp_w = int(fw * scale)
            disp_h = int(fh * scale)
            resized = cv2.resize(frame, (disp_w, disp_h), interpolation=cv2.INTER_AREA)

            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            img_pil = Image.fromarray(rgb)
            self._photo_image = ImageTk.PhotoImage(image=img_pil)

            self.canvas.delete("all")
            cx = (canvas_w - disp_w) // 2
            cy = (canvas_h - disp_h) // 2
            self.canvas.create_image(cx, cy, anchor=tk.NW, image=self._photo_image)
            
            # Draw PIP of last processed image
            if getattr(self.ocr_engine, 'last_processed_image', None) is not None:
                pip = self.ocr_engine.last_processed_image
                if len(pip.shape) == 2:
                    pip_rgb = cv2.cvtColor(pip, cv2.COLOR_GRAY2RGB)
                else:
                    pip_rgb = cv2.cvtColor(pip, cv2.COLOR_BGR2RGB)

                pip_h, pip_w = pip_rgb.shape[:2]
                pip_scale = min(1.0, 400 / max(1, pip_w))
                pip_dw, pip_dh = int(pip_w * pip_scale), int(pip_h * pip_scale)
                
                if pip_dw > 0 and pip_dh > 0:
                    pip_resized = cv2.resize(pip_rgb, (pip_dw, pip_dh))
                    cv2.rectangle(pip_resized, (0, 0), (pip_dw - 1, pip_dh - 1), (0, 255, 0), 2)
                    
                    pip_pil = Image.fromarray(pip_resized)
                    self._pip_image = ImageTk.PhotoImage(image=pip_pil)
                    
                    px = canvas_w - pip_dw - 15
                    py = canvas_h - pip_dh - 15
                    self.canvas.create_image(px, py, anchor=tk.NW, image=self._pip_image)
                    self.canvas.create_text(px, py - 5, text="OCR Deskewed Crop", fill="#00ff80", anchor=tk.SW, font=("Helvetica", 10, "bold"))

        self.after(33, self._update_video_canvas)

    def _log_test_capture(self, code: str, frame: Optional[np.ndarray], status: str):
        if not self.test_mode or not self.test_session_dir:
            return

        self.test_capture_counter += 1
        if status in ("NEW", "MANUAL_NEW") and code not in self.test_captured_codes:
            self.test_captured_codes.append(code)

        info = getattr(self.ocr_engine, 'last_scan_info', {}) or {}
        latency_ms = info.get("latency_ms", "N/A")
        pass_name = info.get("pass", "N/A")
        focus_val = self.focus_controller.config.get("focus_absolute", "N/A")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        raw_name = f"cap_{self.test_capture_counter:03d}_{code}_raw.jpg"
        crop_name = f"cap_{self.test_capture_counter:03d}_{code}_crop.jpg"

        if frame is not None:
            raw_path = os.path.join(self.test_session_dir, raw_name)
            try:
                cv2.imwrite(raw_path, frame)
            except Exception as e:
                logger.warning(f"Failed to save raw frame in test mode: {e}")

        pip = getattr(self.ocr_engine, 'last_processed_image', None)
        if pip is not None:
            crop_path = os.path.join(self.test_session_dir, crop_name)
            try:
                cv2.imwrite(crop_path, pip)
            except Exception as e:
                logger.warning(f"Failed to save crop frame in test mode: {e}")

        if self.test_log_file:
            log_line = (
                f"[{ts}] #{self.test_capture_counter:03d} [{status}] "
                f"Code: {code} | Latency: {latency_ms}ms | Pass: {pass_name} | "
                f"Focus: {focus_val} | Raw: {raw_name} | Crop: {crop_name}\n"
            )
            try:
                with open(self.test_log_file, "a", encoding="utf-8") as f:
                    f.write(log_line)
            except Exception as e:
                logger.warning(f"Failed to write test log line: {e}")

    def _nudge_focus(self, delta: int):
        curr = self.focus_controller.config.get("focus_absolute", 313)
        new_f = max(0, min(1023, curr + delta))
        if self.camera_device_path:
            self.focus_controller.set_focus(self.camera_device_path, new_f)
        self.focus_controller.config["focus_absolute"] = new_f
        self.focus_controller.save_config()
        self.focus_val_lbl.configure(text=str(new_f))
        self._show_banner_info("Focus Adjusted", f"Manual focus set to {new_f}.")

    def _trigger_optimize_focus(self):
        if not self.camera_device_path:
            self._show_banner_duplicate("Focus Error", "No active camera connected.")
            return

        def opt_thread():
            self.event_queue.put(("BANNER_INFO", ("Focus Optimization", "Scanning for peak edge sharpness...")))

            def on_prog(f_val, score):
                self.event_queue.put(("FOCUS_PROGRESS", (f_val, score)))

            best_f = self.focus_controller.optimize_focus(
                self.cap,
                self.camera_device_path,
                get_frame_fn=lambda: self.latest_frame,
                on_progress=on_prog
            )
            self.event_queue.put(("FOCUS_DONE", best_f))

        threading.Thread(target=opt_thread, daemon=True).start()

    def _on_tree_double_click(self, event=None):
        if event and hasattr(event, "y"):
            item_id = self.tree.identify_row(event.y)
        else:
            selected = self.tree.selection()
            item_id = selected[0] if selected else None

        if item_id:
            self.tree.selection_set(item_id)
            self._open_edit_dialog(item_id)

    def _on_tree_right_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if item_id:
            self.tree.selection_set(item_id)
            self.tree_menu.tk_popup(event.x_root, event.y_root)

    def _on_tree_delete_key(self, event=None):
        self._delete_selected_code()

    def _edit_selected_code(self):
        selected = self.tree.selection()
        if not selected:
            return
        self._open_edit_dialog(selected[0])

    def _open_edit_dialog(self, item_id: str):
        values = self.tree.item(item_id, "values")
        if not values:
            return
        idx = int(values[0])
        current_code = str(values[1])

        dialog = tk.Toplevel(self)
        dialog.title(f"Edit Redemption Code #{idx}")
        dialog.geometry("380x180")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 380) // 2
        y = self.winfo_y() + (self.winfo_height() - 180) // 2
        dialog.geometry(f"+{x}+{y}")

        pad_frame = ttk.Frame(dialog, padding="15")
        pad_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(pad_frame, text=f"Edit Code #{idx}:", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=(0, 6))

        code_var = tk.StringVar(value=current_code)
        entry = ttk.Entry(pad_frame, textvariable=code_var, font=("Courier", 12, "bold"))
        entry.pack(fill=tk.X, pady=(0, 8))
        entry.focus_set()
        entry.select_range(0, tk.END)

        err_var = tk.StringVar(value="")
        err_lbl = tk.Label(pad_frame, textvariable=err_var, fg="#dc2626", font=("Helvetica", 8))
        err_lbl.pack(anchor="w", pady=(0, 8))

        btn_box = ttk.Frame(pad_frame)
        btn_box.pack(fill=tk.X)

        def do_save():
            new_val = code_var.get().strip().upper()
            if not new_val:
                err_var.set("Code cannot be empty.")
                return
            success, err_msg = self.storage.update_code(idx, new_val)
            if not success:
                err_var.set(err_msg or "Failed to update code.")
                return

            # Update item in treeview
            orig_vals = list(self.tree.item(item_id, "values"))
            orig_vals[1] = new_val
            orig_vals[3] = "edited"
            self.tree.item(item_id, values=orig_vals)

            self._show_banner_info("Code Updated", f"Code #{idx} changed from {current_code} to {new_val}.")
            dialog.destroy()

        save_btn = ttk.Button(btn_box, text="💾 Save", command=do_save)
        save_btn.pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_box, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

        dialog.bind("<Return>", lambda e: do_save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

    def _delete_selected_code(self):
        selected = self.tree.selection()
        if not selected:
            return
        item_id = selected[0]
        values = self.tree.item(item_id, "values")
        if not values:
            return
        idx = int(values[0])
        code = str(values[1])

        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete code #{idx} ({code})?\nThis change will be saved to disk.",
            parent=self
        )
        if not confirm:
            return

        success, deleted = self.storage.delete_code(idx)
        if success:
            # Refresh entire treeview with re-indexed entries
            self.tree.delete(*self.tree.get_children())
            for entry in self.storage.get_all():
                self._insert_entry_to_tree(entry, scroll=False)
            self._update_stats()
            self._show_banner_info("Code Deleted", f"Deleted code #{idx} ({code}). Remaining codes re-indexed.")
        else:
            messagebox.showerror("Delete Error", f"Could not delete code #{idx}.")

    def _manual_scan_trigger(self):
        frame = None
        with self.frame_lock:
            if self.latest_frame is not None:
                frame = self.latest_frame.copy()

        if frame is None:
            self._show_banner_duplicate("Capture Failed", "No camera frame available.")
            return

        codes = self.ocr_engine.scan_image(frame)
        if not codes:
            self._show_banner_info("No Code Found", "Could not detect redemption code in the guide box.")
            return

        for c in codes:
            is_new, entry, orig = self.storage.add_code(c)
            if is_new:
                self.session_new_count += 1
                self._insert_entry_to_tree(entry, scroll=True)
                self._update_stats()
                self._show_banner_success(f"CAPTURED: {entry.code}", f"Manual scan captured code #{entry.index}.")
                if self.test_mode:
                    self._log_test_capture(entry.code, frame, status="MANUAL_NEW")
            else:
                self._handle_duplicate_code(c, frame)

    def _choose_and_load_file(self):
        path = filedialog.askopenfilename(
            title="Open Redemption Codes File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return

        self.storage.clear()
        self.tree.delete(*self.tree.get_children())
        count = self.storage.load_from_file(path)
        for entry in self.storage.get_all():
            self._insert_entry_to_tree(entry, scroll=False)

        self.filepath_var.set(f"File: {self.storage.filepath}")
        self._update_stats()
        self._show_banner_info("File Loaded", f"Loaded {count} codes from {os.path.basename(path)}.")

    def _save_as_file(self):
        path = filedialog.asksaveasfilename(
            title="Save Redemption Codes As",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return

        if self.storage.save_to_file(path):
            self.filepath_var.set(f"File: {self.storage.filepath}")
            self._show_banner_success("Saved File", f"All codes saved to {os.path.basename(path)}.")
        else:
            messagebox.showerror("Save Error", f"Failed to save file to {path}")

    def _copy_selected_code(self):
        selected = self.tree.selection()
        if not selected:
            return
        item = self.tree.item(selected[0])
        code = item['values'][1]
        self.clipboard_clear()
        self.clipboard_append(str(code))
        self._show_banner_info("Copied", f"Copied {code} to clipboard.")

    def _clear_all_codes(self):
        if messagebox.askyesno("Confirm Clear", "Clear all captured codes from memory? (File will not be overwritten until saved)"):
            self.storage.clear()
            self.tree.delete(*self.tree.get_children())
            self.duplicate_count = 0
            self.session_new_count = 0
            self._update_stats()
            self._show_banner_info("Cleared", "List cleared.")

    def _on_close(self):
        self._stop_camera()
        if self.test_mode:
            test_txt_path = os.path.join(os.getcwd(), "captured_codes_test.txt")
            try:
                with open(test_txt_path, "w", encoding="utf-8") as f:
                    for entry in self.storage.get_all():
                        f.write(f"{entry.to_line()}\n")
                logger.info(f"Test Mode: Saved {self.storage.count()} codes to {test_txt_path}")
                if self.test_log_file:
                    with open(self.test_log_file, "a", encoding="utf-8") as f:
                        f.write(f"=== TEST RUN FINISHED AT {datetime.now()} | Total Codes: {self.storage.count()} ===\n")
            except Exception as e:
                logger.error(f"Error saving captured_codes_test.txt: {e}")
        self.destroy()


def run_app():
    app = ScannerApp()
    app.mainloop()


if __name__ == "__main__":
    run_app()
