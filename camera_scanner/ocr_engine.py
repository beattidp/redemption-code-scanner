"""OCR Engine for package redemption code extraction and debounced scanning."""

import re
import time
import logging
import cv2
import numpy as np
import pytesseract
from dataclasses import dataclass
from typing import Optional, List, Tuple, Callable, Dict, Any

logger = logging.getLogger(__name__)

# Strict 5-4-4 pattern matching redemption codes (e.g., GST8S-JLPZ-KKXK, JXVTS-DF9K-GSFV, PTX57-DF5P-9MJD)
DEFAULT_PATTERN = r'([A-Z0-9]{5})[\s\-_—–]+([A-Z0-9]{4})[\s\-_—–]+([A-Z0-9]{4})'


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev[j + 1] + 1
            deletions = curr[j] + 1
            substitutions = prev[j] + (c1 != c2)
            curr.append(min(insertions, deletions, substitutions))
        prev = curr
    return prev[-1]


@dataclass
class ROI:
    """Normalized Region of Interest (0.0 to 1.0 coordinates)."""
    # Extended to edges of frame (w=1.0) with upper 60% eliminated to isolate code pill
    x: float = 0.0
    y: float = 0.472
    w: float = 1.0
    h: float = 0.128

    def get_pixel_crop(self, frame: np.ndarray) -> np.ndarray:
        fh, fw = frame.shape[:2]
        x1 = max(0, min(int(self.x * fw), fw - 1))
        y1 = max(0, min(int(self.y * fh), fh - 1))
        x2 = max(x1 + 10, min(int((self.x + self.w) * fw), fw))
        y2 = max(y1 + 10, min(int((self.y + self.h) * fh), fh))
        return frame[y1:y2, x1:x2]

    def get_pixel_box(self, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
        return (
            int(self.x * frame_w),
            int(self.y * frame_h),
            int(self.w * frame_w),
            int(self.h * frame_h)
        )


class OCREngine:
    def __init__(
        self,
        pattern: str = DEFAULT_PATTERN,
        cooldown_seconds: float = 4.0,
        required_confirmations: int = 2,
        max_jitter_distance: int = 2
    ):
        self.pattern_str = pattern
        self.regex = re.compile(pattern, re.IGNORECASE)
        self.roi = ROI()
        self.use_roi: bool = True
        self.cooldown_seconds: float = cooldown_seconds
        self.required_confirmations: int = required_confirmations
        self.max_jitter_distance: int = max_jitter_distance

        # Package presence state
        self.active_present_code: Optional[str] = None
        self.last_seen_time: float = 0.0
        self.consecutive_empty_frames: int = 0

        # Confirmation buffer for new candidate arrivals
        self.candidate_history = []
        self.history_max_size = 10
        self.last_processed_image: Optional[np.ndarray] = None
        self.last_scan_info: Dict[str, Any] = {}

    def set_pattern(self, pattern: str) -> None:
        self.pattern_str = pattern
        self.regex = re.compile(pattern, re.IGNORECASE)

    def extract_codes_from_text(self, text: str) -> List[str]:
        codes = []
        clean = text.strip().replace(' ', '-').replace('_', '-').replace('—', '-')
        for match in self.regex.finditer(clean.upper()):
            if match.lastindex and match.lastindex >= 3:
                code = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            else:
                raw = match.group(0).strip().upper()
                code = re.sub(r'[\s_—–]+', '-', raw)
            parts = code.split('-')
            if len(parts) == 3 and len(parts[0]) == 5 and len(parts[1]) == 4 and len(parts[2]) == 4:
                codes.append(code)
        return list(dict.fromkeys(codes))

    def scan_image(self, frame: np.ndarray) -> List[str]:
        """
        Scan image for redemption codes using tiered multi-pass strategy:
        Pass 1: Median Blur + PSM 7 (fast, bridges dot-matrix / halftone, clean character edges).
        Pass 2: Bilateral Filter + PSM 7 fallback (sharp corners, preserves 5, 6, 7).
        Pass 3: Median Blur + PSM 6 fallback.
        Pass 4: Bilateral Filter + PSM 6 fallback.
        """
        t_start = time.time()
        if frame is None or frame.size == 0:
            return []

        target_img = self.roi.get_pixel_crop(frame) if self.use_roi else frame
        if target_img.size == 0:
            return []

        # Fast check for blank or very low contrast frames
        if len(target_img.shape) == 3:
            gray = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY)
        else:
            gray = target_img

        if np.std(gray) < 5.0:
            return []

        # Find the bright white label and crop it tightly to remove cardboard/background noise
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        valid_contours = []
        for c in contours:
            rect = cv2.minAreaRect(c)
            center, size, angle = rect
            w, h = size
            if w < h:
                w, h = h, w
                angle += 90
            # Ensure it's a wide rectangle of significant size (like a code pill/label)
            if w > 100 and h > 15 and w > h * 1.8:
                valid_contours.append((w * h, rect))

        if valid_contours:
            valid_contours.sort(reverse=True)
            _, rect = valid_contours[0]
            center, size, angle = rect
            w, h = size
            if w < h:
                w, h = h, w
                angle += 90

            # Rotate image to level the text (deskewing)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(gray, M, (gray.shape[1], gray.shape[0]), borderValue=255)

            # Crop tightly within the off-white pill region without adding artificial white borders
            margin = max(2, min(4, int(h * 0.03)))
            cw = max(1, int(w) - 2 * margin)
            ch = max(1, int(h) - 2 * margin)
            raw_crop = cv2.getRectSubPix(rotated, (cw, ch), center)

            # Upscale only if height is low (<45px) to prevent stroke-thickening artifacts on high-res pills
            processed_crop = cv2.resize(raw_crop, (0, 0), fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC) if raw_crop.shape[0] < 45 else raw_crop
        else:
            # Fallback to direct ROI crop
            processed_crop = cv2.resize(gray, (0, 0), fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC) if gray.shape[0] < 45 else gray

        # Pass 1: Median Blur PSM 6 (bridges dot-matrix / halftone, accurate digit aspect ratio without baseline distortion)
        med_crop = cv2.medianBlur(processed_crop, 3)
        self.last_processed_image = med_crop.copy()

        cfg6 = '--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'
        cfg7 = '--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'

        try:
            txt_med6 = pytesseract.image_to_string(med_crop, config=cfg6, timeout=1.4)
            codes = self.extract_codes_from_text(txt_med6)
            if codes:
                self.last_scan_info = {
                    "code": codes[0],
                    "latency_ms": round((time.time() - t_start) * 1000, 1),
                    "pass": "Pass 1: Median PSM 6"
                }
                return codes
        except Exception as e:
            logger.debug(f"Median PSM 6 OCR error: {e}")

        # Pass 2: Edge-preserving Bilateral Filter + PSM 6 (preserves sharp right angles, prevents rounding 5)
        bi_crop = cv2.bilateralFilter(processed_crop, 5, 50, 50)
        try:
            txt_bi6 = pytesseract.image_to_string(bi_crop, config=cfg6, timeout=1.4)
            codes = self.extract_codes_from_text(txt_bi6)
            if codes:
                self.last_processed_image = bi_crop.copy()
                self.last_scan_info = {
                    "code": codes[0],
                    "latency_ms": round((time.time() - t_start) * 1000, 1),
                    "pass": "Pass 2: Bilateral PSM 6"
                }
                return codes
        except Exception as e:
            logger.debug(f"Bilateral PSM 6 OCR error: {e}")

        # Pass 3: Median Blur with PSM 7 (single line mode fallback)
        try:
            txt_med7 = pytesseract.image_to_string(med_crop, config=cfg7, timeout=1.2)
            codes = self.extract_codes_from_text(txt_med7)
            if codes:
                self.last_scan_info = {
                    "code": codes[0],
                    "latency_ms": round((time.time() - t_start) * 1000, 1),
                    "pass": "Pass 3: Median PSM 7"
                }
                return codes
        except Exception as e:
            logger.debug(f"Median PSM 7 OCR error: {e}")

        # Pass 4: Bilateral Filter with PSM 7 fallback
        try:
            txt_bi7 = pytesseract.image_to_string(bi_crop, config=cfg7, timeout=1.2)
            codes = self.extract_codes_from_text(txt_bi7)
            if codes:
                self.last_processed_image = bi_crop.copy()
                self.last_scan_info = {
                    "code": codes[0],
                    "latency_ms": round((time.time() - t_start) * 1000, 1),
                    "pass": "Pass 4: Bilateral PSM 7"
                }
                return codes
        except Exception as e:
            logger.debug(f"Bilateral PSM 7 OCR error: {e}")

        return []

    def process_frame(
        self,
        frame: np.ndarray,
        on_new_code: Optional[Callable[[str], None]] = None,
        on_duplicate: Optional[Callable[[str], None]] = None,
        is_known_callback: Optional[Callable[[str], bool]] = None
    ) -> Optional[str]:
        now = time.time()
        detected_codes = self.scan_image(frame)

        if not detected_codes:
            self.consecutive_empty_frames += 1
            # Package removal cooldown check: requires both elapsed cooldown AND multiple consecutive empty frames
            if (
                self.active_present_code is not None
                and self.consecutive_empty_frames >= 4
                and (now - self.last_seen_time) > self.cooldown_seconds
            ):
                self.active_present_code = None
                self.candidate_history.clear()
            return None

        self.consecutive_empty_frames = 0
        raw_code = detected_codes[0]

        # 1. Jitter Guard: Check if within edit distance <= 2 of the currently resting package
        if self.active_present_code is not None:
            dist = levenshtein_distance(raw_code, self.active_present_code)
            if dist <= self.max_jitter_distance:
                # Same package is still in front of camera
                self.last_seen_time = now
                self.candidate_history.clear()
                return self.active_present_code

        # 2. Package changed or newly presented: Confirm over multiple frames
        self.candidate_history.append(raw_code)
        if len(self.candidate_history) > self.history_max_size:
            self.candidate_history.pop(0)

        if self.candidate_history.count(raw_code) >= self.required_confirmations:
            confirmed_code = raw_code
            self.active_present_code = confirmed_code
            self.last_seen_time = now
            self.candidate_history.clear()

            is_known = is_known_callback(confirmed_code) if is_known_callback else False
            if is_known:
                if on_duplicate:
                    on_duplicate(confirmed_code)
            else:
                if on_new_code:
                    on_new_code(confirmed_code)

            return confirmed_code

        return None
