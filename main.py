"""Main entrypoint for Redemption Code Scanner application."""

import argparse
import sys
import logging
from camera_scanner.gui import ScannerApp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

def main():
    parser = argparse.ArgumentParser(
        description="Redemption Code Scanner for packages placed in USB Camera 1e45:0209 view."
    )
    parser.add_argument(
        "--file", "-f",
        default="captured_codes.txt",
        help="Path to the text file for loading and saving codes (default: captured_codes.txt)"
    )
    parser.add_argument(
        "--test", "-t",
        action="store_true",
        help="Run in test mode: saves original camera view, processed preview crops, timing metadata, and captured_codes_test.txt on exit."
    )
    args = parser.parse_args()

    app = ScannerApp(default_file=args.file, test_mode=args.test)
    app.mainloop()

if __name__ == '__main__':
    main()
