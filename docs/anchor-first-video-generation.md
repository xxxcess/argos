# Anchor-first video generation

Argos generates a stable, muted video by creating a Gallery image anchor first,
then animating that exact image through local `mlx-video` / LTX-2.

## Flow

1. The user writes a high-level video intent.
2. Argos calls the configured **Utility Model** to derive a detailed still-image
   art-direction prompt. The original intent is not sent to image generation.
3. Argos uses the current user's **Image Default**. This can be the local
   Diffusers server started from Cookbook or any compatible image endpoint.
4. The anchor is persisted to generated media and Gallery. The job reports
   `anchor_ready`, so the UI refreshes Gallery and tells the user animation is
   continuing.
5. Argos calls the Utility Model again to derive a separate motion/camera prompt.
   The original intent is not sent to `mlx-video`.
6. Local LTX image-to-video produces a silent MP4 and adds it to Gallery with
   links to the anchor and both derived prompts.

## Stable v1 profile

- native macOS on Apple Silicon
- LTX-2 distilled
- generated image anchor only
- 512 × 512 image and video
- 241 frames at 24 FPS, approximately 10 seconds
- one local video job at a time
- silent MP4 only

## Setup

The Image Default is configured through the existing AI Defaults / Cookbook
flow. For a local image endpoint, start the Cookbook Diffusers image server and
select it as the Image Default first.

Install the video runtime only in a native macOS virtual environment:

```bash
python -m pip install -r requirements-mlx-video-macos.txt
```

The default executable is `mlx_video.ltx_2.generate`. Set `MLX_VIDEO_BIN` only
when the installed executable uses a different name or location.

## Privacy and reproducibility

Jobs keep the source intent for audit, but only the planner sees it. The image
endpoint receives the derived anchor prompt; LTX receives the separate derived
motion prompt plus a private job-local copy of the Gallery anchor. Gallery video
metadata records the anchor, derived prompts, planner model, seed, and fixed
output profile.
