"""Redraw the bundled sample designs and rooms, then calibrate the rooms.

Usage: python scripts/make_samples.py
"""
from pathlib import Path

from podagent.images import calibrate, samples

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    samples.write_all(ROOT / "samples/designs", ROOT / "samples/rooms")
    for name, r in calibrate.calibrate_rooms(ROOT / "samples/rooms").items():
        print(name, r["ratio_measured"], r["corners"], *r["warnings"])
