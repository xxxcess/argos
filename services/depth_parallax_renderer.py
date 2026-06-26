"""Low-memory local renderer for depth-aware parallax clips.

A generated anchor is kept visually stable: Depth Anything V2 Small estimates a
single relative-depth map, then deterministic camera parallax warps the anchor
at a low source frame rate. FFmpeg motion interpolation produces the final 24
FPS silent MP4.  There is no heavyweight text-to-video model or cloud request.
"""
from __future__ import annotations

import math
import os
import subprocess
import time
from pathlib import Path
from typing import Callable


class RenderCancelled(RuntimeError):
    pass


def camera_plan(motion_prompt: str, seed: int) -> dict[str, float | str]:
    """Derive a bounded camera path from the *derived* motion prompt only."""
    text = (motion_prompt or "").lower()
    # Default remains deliberately subtle: the goal is a believable living
    # still, not an unstable simulation of new subject movement.
    direction = "right"
    if any(token in text for token in ("pan left", "drift left", "move left")):
        direction = "left"
    elif any(token in text for token in ("pan up", "tilt up", "rise")):
        direction = "up"
    elif any(token in text for token in ("pan down", "tilt down", "lower")):
        direction = "down"
    elif any(token in text for token in ("pull back", "dolly out", "recede", "zoom out")):
        direction = "out"
    elif any(token in text for token in ("push in", "dolly in", "move closer", "zoom in")):
        direction = "in"
    # Tiny deterministic variation prevents every clip from having identical
    # timing while retaining reproducibility from the stored seed.
    wobble = ((int(seed) % 17) - 8) / 8000.0
    return {"direction": direction, "strength": 0.030 + abs(wobble), "wobble": wobble}


def _estimate_depth(image_path: Path, model_path: Path):
    """Return normalized depth in the image's native pixel dimensions."""
    import numpy as np
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    image = Image.open(image_path).convert("RGB")
    processor = AutoImageProcessor.from_pretrained(str(model_path), local_files_only=True)
    model = AutoModelForDepthEstimation.from_pretrained(str(model_path), local_files_only=True)
    device = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
    try:
        model.to(device)
        model.eval()
        inputs = processor(images=image, return_tensors="pt")
        inputs = {name: value.to(device) for name, value in inputs.items()}
        with torch.no_grad():
            prediction = model(**inputs).predicted_depth
        prediction = F.interpolate(
            prediction.unsqueeze(1),
            size=(image.height, image.width),
            mode="bicubic",
            align_corners=False,
        ).squeeze().float().cpu().numpy()
    finally:
        # Release MPS allocations between serial jobs on an 8 GB machine.
        del model
        if device == "mps":
            try:
                torch.mps.empty_cache()
            except Exception:
                pass
    low, high = float(np.percentile(prediction, 2)), float(np.percentile(prediction, 98))
    if high <= low:
        depth = np.full_like(prediction, 0.5, dtype=np.float32)
    else:
        depth = np.clip((prediction - low) / (high - low), 0.0, 1.0).astype(np.float32)
    return image, depth


def _render_frame(rgb, depth, t: float, plan: dict[str, float | str]):
    """Backward-map one image using a shallow depth-dependent camera shift."""
    import numpy as np
    from PIL import Image

    height, width = rgb.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    eased = 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))
    direction = str(plan["direction"])
    strength = float(plan["strength"])
    wobble = float(plan["wobble"])
    # Shift is intentionally small. Larger values reveal disocclusion gaps that
    # require expensive generative inpainting and are unsuitable for 8 GB hosts.
    pan_x = pan_y = zoom = 0.0
    if direction == "left": pan_x = -strength * eased
    elif direction == "right": pan_x = strength * eased
    elif direction == "up": pan_y = -strength * eased
    elif direction == "down": pan_y = strength * eased
    elif direction == "in": zoom = strength * 0.45 * eased
    elif direction == "out": zoom = -strength * 0.32 * eased
    pan_x += wobble * math.sin(math.pi * t)
    pan_y += wobble * 0.5 * math.sin(math.pi * t)
    scale = max(0.94, 1.0 + zoom)
    centered_depth = depth - 0.5
    source_x = (xx - (width / 2.0)) / scale + (width / 2.0) - (pan_x * width * (1.0 + centered_depth * 1.6))
    source_y = (yy - (height / 2.0)) / scale + (height / 2.0) - (pan_y * height * (1.0 + centered_depth * 1.6))
    source_x = np.clip(np.rint(source_x), 0, width - 1).astype(np.int32)
    source_y = np.clip(np.rint(source_y), 0, height - 1).astype(np.int32)
    return Image.fromarray(rgb[source_y, source_x], mode="RGB")


def render_depth_parallax_video(
    *,
    anchor_path: Path,
    output_path: Path,
    model_path: Path,
    ffmpeg_path: str,
    motion_prompt: str,
    seed: int,
    source_fps: int = 6,
    duration_seconds: int = 10,
    target_fps: int = 24,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Create a fixed 10-second, muted 512px MP4 using local resources only."""
    import numpy as np
    from PIL import Image

    if cancelled and cancelled():
        raise RenderCancelled("Video generation was cancelled.")
    image, depth = _estimate_depth(anchor_path, model_path)
    image = image.resize((512, 512), Image.Resampling.LANCZOS)
    # Estimate depth at original size then resize it together with the anchor.
    depth_image = Image.fromarray(np.clip(depth * 255.0, 0, 255).astype("uint8"), mode="L")
    depth = np.asarray(depth_image.resize((512, 512), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0
    rgb = np.asarray(image, dtype=np.uint8)
    plan = camera_plan(motion_prompt, seed)
    frame_dir = output_path.parent / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_count = int(source_fps) * int(duration_seconds)
    for index in range(frame_count):
        if cancelled and cancelled():
            raise RenderCancelled("Video generation was cancelled.")
        t = index / max(1, frame_count - 1)
        _render_frame(rgb, depth, t, plan).save(frame_dir / f"frame_{index:04d}.png", "PNG", optimize=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_graph = f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,format=yuv420p"
    command = [
        ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(source_fps), "-i", str(frame_dir / "frame_%04d.png"),
        "-vf", filter_graph,
        "-t", str(duration_seconds), "-an", "-movflags", "+faststart",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output_path),
    ]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        while process.poll() is None:
            if cancelled and cancelled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RenderCancelled("Video generation was cancelled.")
            time.sleep(0.2)
        stderr = process.stderr.read() if process.stderr else ""
        if process.returncode != 0 or not output_path.is_file() or output_path.stat().st_size < 1024:
            raise RuntimeError((stderr or "FFmpeg could not render the parallax video.")[:700])
    finally:
        if process.stderr:
            process.stderr.close()
