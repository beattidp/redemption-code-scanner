import re
with open('camera_scanner/ocr_engine.py', 'r') as f:
    content = f.read()

init_replacement = """        # Confirmation buffer for new candidate arrivals
        self.candidate_history = []
        self.history_max_size = 10"""
content = re.sub(
    r'# Confirmation buffer for new candidate arrivals\s+self\.pending_candidate: Optional\[str\] = None\s+self\.candidate_hits: int = 0',
    init_replacement,
    content
)

clear_replacement = """                self.active_present_code = None
                self.candidate_history.clear()
            return None"""
content = re.sub(
    r'                self\.active_present_code = None\s+self\.pending_candidate = None\s+self\.candidate_hits = 0\s+return None',
    clear_replacement,
    content
)

jitter_replacement = """                # Same package is still in front of camera
                self.last_seen_time = now
                self.candidate_history.clear()
                return self.active_present_code"""
content = re.sub(
    r'                # Same package is still in front of camera\s+self\.last_seen_time = now\s+self\.pending_candidate = None\s+self\.candidate_hits = 0\s+return self\.active_present_code',
    jitter_replacement,
    content
)

confirm_logic_original = """        # 2. Package changed or newly presented: Confirm over multiple frames
        if raw_code == self.pending_candidate:
            self.candidate_hits += 1
        else:
            self.pending_candidate = raw_code
            self.candidate_hits = 1

        if self.candidate_hits >= self.required_confirmations:
            confirmed_code = self.pending_candidate
            self.active_present_code = confirmed_code
            self.last_seen_time = now
            self.pending_candidate = None
            self.candidate_hits = 0"""

confirm_logic_new = """        # 2. Package changed or newly presented: Confirm over multiple frames
        self.candidate_history.append(raw_code)
        if len(self.candidate_history) > self.history_max_size:
            self.candidate_history.pop(0)

        if self.candidate_history.count(raw_code) >= self.required_confirmations:
            confirmed_code = raw_code
            self.active_present_code = confirmed_code
            self.last_seen_time = now
            self.candidate_history.clear()"""

content = content.replace(confirm_logic_original, confirm_logic_new)

with open('camera_scanner/ocr_engine.py', 'w') as f:
    f.write(content)
