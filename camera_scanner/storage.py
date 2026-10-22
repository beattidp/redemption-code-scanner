"""Storage management for captured redemption codes, duplicate tracking, and text file persistence."""

import os
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Set, Optional, Dict, Tuple


@dataclass
class CodeEntry:
    code: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    index: int = 0
    source: str = "scanned"  # "loaded" or "scanned"

    def to_line(self) -> str:
        """Format as simple text line for code storage."""
        return self.code


class CodeStorage:
    def __init__(self, default_filepath: str = "captured_codes.txt"):
        self.filepath = os.path.abspath(default_filepath)
        self.lock = threading.Lock()
        self.entries: List[CodeEntry] = []
        self._code_set: Set[str] = set()
        self._code_map: Dict[str, CodeEntry] = {}

    def normalize(self, code: str) -> str:
        """Normalize code string: strip whitespace and uppercase."""
        return code.strip().upper()

    def load_from_file(self, filepath: Optional[str] = None) -> int:
        """
        Load codes from text file.
        Returns the number of new unique codes loaded.
        """
        target_path = os.path.abspath(filepath) if filepath else self.filepath
        self.filepath = target_path

        if not os.path.exists(target_path):
            return 0

        loaded_count = 0
        with self.lock:
            with open(target_path, "r", encoding="utf-8") as f:
                for line in f:
                    raw = line.strip()
                    if not raw or raw.startswith("#"):
                        continue
                    # In case line contains timestamp like: "GST8S-JLPZ-KKXK (2026-...)" or just code
                    code_part = raw.split()[0]
                    norm = self.normalize(code_part)
                    if norm and norm not in self._code_set:
                        idx = len(self.entries) + 1
                        entry = CodeEntry(code=norm, index=idx, source="loaded")
                        self.entries.append(entry)
                        self._code_set.add(norm)
                        self._code_map[norm] = entry
                        loaded_count += 1
        return loaded_count

    def add_code(self, code: str) -> Tuple[bool, CodeEntry, Optional[CodeEntry]]:
        """
        Attempt to add a code.
        Returns:
            (is_new, current_or_new_entry, original_duplicate_entry)
            If is_new is True: (True, new_entry, None)
            If is_new is False: (False, existing_entry, existing_entry)
        """
        norm = self.normalize(code)
        if not norm:
            raise ValueError("Code cannot be empty")

        with self.lock:
            if norm in self._code_set:
                existing = self._code_map[norm]
                return False, existing, existing

            # New code
            idx = len(self.entries) + 1
            new_entry = CodeEntry(code=norm, index=idx, source="scanned")
            self.entries.append(new_entry)
            self._code_set.add(norm)
            self._code_map[norm] = new_entry

            # Immediately append to disk file for crash resilience
            self._append_to_file(new_entry)
            return True, new_entry, None

    def _append_to_file(self, entry: CodeEntry) -> None:
        """Append a single new code to the active file."""
        try:
            parent_dir = os.path.dirname(self.filepath)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(f"{entry.to_line()}\n")
        except OSError as e:
            # Keep in-memory even if disk write fails
            print(f"Error appending to file {self.filepath}: {e}")

    def save_to_file(self, target_filepath: Optional[str] = None) -> bool:
        """Save the entire list of codes to the target or current file."""
        dest = os.path.abspath(target_filepath) if target_filepath else self.filepath
        with self.lock:
            self.filepath = dest
            return self._save_unlocked()

    def _save_unlocked(self) -> bool:
        """Internal helper to write entries to filepath without acquiring self.lock."""
        try:
            parent_dir = os.path.dirname(self.filepath)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            with open(self.filepath, "w", encoding="utf-8") as f:
                for entry in self.entries:
                    f.write(f"{entry.to_line()}\n")
            return True
        except OSError as e:
            print(f"Error saving to file {self.filepath}: {e}")
            return False

    def update_code(self, index: int, new_code: str) -> Tuple[bool, Optional[str]]:
        """
        Update code at 1-based index with a new code value.
        Returns: (success, error_message_if_any)
        """
        norm = self.normalize(new_code)
        if not norm:
            return False, "Code cannot be empty"

        with self.lock:
            if index < 1 or index > len(self.entries):
                return False, f"Index {index} out of range"

            entry = self.entries[index - 1]
            old_code = entry.code
            if old_code == norm:
                return True, None

            if norm in self._code_set:
                return False, f"Code '{norm}' already exists at index #{self._code_map[norm].index}"

            self._code_set.remove(old_code)
            del self._code_map[old_code]

            entry.code = norm
            entry.source = "edited"
            self._code_set.add(norm)
            self._code_map[norm] = entry

            self._save_unlocked()
            return True, None

    def delete_code(self, index: int) -> Tuple[bool, Optional[CodeEntry]]:
        """
        Delete code at 1-based index and re-index remaining entries.
        Returns: (success, deleted_entry)
        """
        with self.lock:
            if index < 1 or index > len(self.entries):
                return False, None

            entry = self.entries.pop(index - 1)
            old_code = entry.code
            if old_code in self._code_set:
                self._code_set.remove(old_code)
            if old_code in self._code_map:
                del self._code_map[old_code]

            self._code_map.clear()
            for i, e in enumerate(self.entries, start=1):
                e.index = i
                self._code_map[e.code] = e

            self._save_unlocked()
            return True, entry

    def get_all(self) -> List[CodeEntry]:
        """Return a copy of all entries."""
        with self.lock:
            return list(self.entries)

    def count(self) -> int:
        with self.lock:
            return len(self.entries)

    def is_duplicate(self, code: str) -> bool:
        norm = self.normalize(code)
        with self.lock:
            return norm in self._code_set

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()
            self._code_set.clear()
            self._code_map.clear()
