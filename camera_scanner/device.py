"""Camera device discovery and resolution for USB cameras."""

import glob
import os
import cv2
import logging
from typing import Optional, Tuple, List, Dict

logger = logging.getLogger(__name__)

TARGET_VENDOR = "1e45"
TARGET_PRODUCT = "0209"

def get_usb_ids_for_node(sysfs_node: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Traverse sysfs to find vendor, product, and device name for a video4linux node."""
    cur = os.path.realpath(sysfs_node)
    vendor, product, name = None, None, None

    # Try reading the node's name
    name_file = os.path.join(sysfs_node, "name")
    if os.path.exists(name_file):
        try:
            with open(name_file, "r") as f:
                name = f.read().strip()
        except OSError:
            pass

    # Traverse up the tree to find idVendor and idProduct
    while cur and cur != "/":
        v_path = os.path.join(cur, "idVendor")
        p_path = os.path.join(cur, "idProduct")
        if os.path.exists(v_path) and os.path.exists(p_path):
            try:
                with open(v_path, "r") as f:
                    vendor = f.read().strip().lower()
                with open(p_path, "r") as f:
                    product = f.read().strip().lower()
                break
            except OSError:
                pass
        cur = os.path.dirname(cur)

    return vendor, product, name


def is_valid_capture_node(dev_path_or_index: int | str) -> bool:
    """Test if a camera node can be opened for video capture."""
    cap = cv2.VideoCapture(dev_path_or_index, cv2.CAP_V4L2)
    if not cap.isOpened():
        return False
    ret, _ = cap.read()
    cap.release()
    return ret


def list_available_cameras() -> List[Dict[str, any]]:
    """List all V4L2 video devices with their USB IDs and names."""
    devices = []
    for path in sorted(glob.glob("/sys/class/video4linux/video*")):
        basename = os.path.basename(path)
        try:
            index = int(basename.replace("video", ""))
        except ValueError:
            continue
        dev_node = f"/dev/{basename}"
        vendor, product, name = get_usb_ids_for_node(path)
        devices.append({
            "index": index,
            "device_node": dev_node,
            "vendor_id": vendor,
            "product_id": product,
            "name": name or basename
        })
    return devices


def find_target_camera(vendor_id: str = TARGET_VENDOR, product_id: str = TARGET_PRODUCT) -> Tuple[Optional[int], Optional[str]]:
    """
    Find video device corresponding to the specified USB vendor and product ID.
    Returns (video_index, device_path) or (None, None) if not found.
    """
    target_v = vendor_id.lower()
    target_p = product_id.lower()

    candidates = []
    for path in sorted(glob.glob("/sys/class/video4linux/video*")):
        basename = os.path.basename(path)
        try:
            index = int(basename.replace("video", ""))
        except ValueError:
            continue
        dev_node = f"/dev/{basename}"
        vendor, product, name = get_usb_ids_for_node(path)

        if vendor == target_v and product == target_p:
            candidates.append((index, dev_node))

    if not candidates:
        logger.warning(f"Target camera {vendor_id}:{product_id} not found in sysfs.")
        return None, None

    # Check candidates for valid video capture (often first node is capture, second is metadata)
    for index, dev_node in candidates:
        if is_valid_capture_node(index):
            logger.info(f"Matched camera {vendor_id}:{product_id} on {dev_node} (index {index})")
            return index, dev_node

    # Fallback to the first candidate if capture test failed
    return candidates[0]
