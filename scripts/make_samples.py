"""Redraw the bundled sample designs, and rebuild the sample rooms' calibration overlay.

Usage: python scripts/make_samples.py
"""

from pathlib import Path

from podagent.images import calibrate, samples

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    samples.write_designs(ROOT / "samples/designs")
    print(calibrate.overlay_sheet(ROOT / "samples/rooms"))
