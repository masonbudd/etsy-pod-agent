"""Upscale a design with Real-ESRGAN, or fall back to a plain resize when it isn't installed."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image

EXE_NAMES = ["realesrgan-ncnn-vulkan", "realesrgan-ncnn-vulkan.exe"]


def find_realesrgan(env: dict) -> Path | None:
    if env.get("REALESRGAN_PATH") and Path(env["REALESRGAN_PATH"]).is_file():
        return Path(env["REALESRGAN_PATH"])
    for name in EXE_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def choose_engine(cfg: dict, env: dict) -> tuple[str, Path | None]:
    want = cfg.get("upscale", {}).get("engine", "auto")
    exe = find_realesrgan(env) if want in ("auto", "realesrgan") else None
    if want == "realesrgan" and exe is None:
        raise FileNotFoundError(
            "upscale.engine is 'realesrgan' but realesrgan-ncnn-vulkan was not found. "
            "Set REALESRGAN_PATH in .env or put it on PATH."
        )
    return ("realesrgan", exe) if exe else ("resize", None)


def upscale(src: Path, dst: Path, cfg: dict, env: dict, run=subprocess.run) -> dict:
    """Write dst at `factor` times the size of src. Returns what was done, for the report."""
    up = cfg.get("upscale", {})
    factor = int(up.get("factor", 4))
    engine, exe = choose_engine(cfg, env)
    dst.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if engine == "realesrgan":
        cmd = [
            str(exe),
            "-i",
            str(src),
            "-o",
            str(dst),
            "-n",
            up.get("model", "realesrgan-x4plus"),
            "-s",
            str(factor),
            "-t",
            str(up.get("tile", 256)),
        ]
        r = run(cmd, cwd=str(exe.parent), capture_output=True)
        if r.returncode != 0 or not dst.exists():
            err = (
                (r.stderr or b"").decode(errors="replace")[-400:]
                if isinstance(r.stderr, bytes)
                else str(r.stderr)[-400:]
            )
            raise RuntimeError(f"Real-ESRGAN failed on {src.name}: {err}")
    else:
        img = Image.open(src).convert("RGB")
        img.resize((img.width * factor, img.height * factor), Image.LANCZOS).save(dst)
    return {"engine": engine, "factor": factor, "seconds": round(time.perf_counter() - t0, 1)}
