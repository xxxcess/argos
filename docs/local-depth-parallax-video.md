# Local depth-parallax video

This is Argos's low-memory local video path for Apple-Silicon Macs such as an
M2 Mac mini with 8 GB unified memory. It creates a **stable, silent 10-second
clip** from a generated Gallery image anchor. It does not claim to be a large
generative image-to-video model and does not invent new subject actions.

## What happens

1. A user writes a high-level video intent in the Video Generation card or asks
the enabled **Generate video** agent tool.
2. The selected Utility Model derives a detailed art-direction prompt. The raw
intent is retained for audit but never passed unchanged to downstream image
generation.
3. Argos uses the same configured **Image Default** as the image-generation
pipeline. A local Cookbook Diffusers endpoint is supported because it is an
ordinary selected Image Default.
4. The generated anchor is saved to Gallery and shown immediately while video
rendering continues.
5. The Utility Model derives a separate, bounded camera-motion prompt.
6. Depth Anything V2 Small estimates one relative-depth map for the anchor.
7. Argos renders subtle deterministic 2.5D parallax frames and uses FFmpeg
motion interpolation to output a muted 512×512 H.264 MP4 at 24 FPS.
8. The video is saved to Gallery with anchor, prompt, seed, and renderer
provenance.

## Why this profile

The renderer performs one small depth-estimation pass and deterministic image
warping. It is deliberately bounded to avoid the memory pressure of 2B/19B
video diffusion models on an 8 GB Mac. The visual result is best described as
an animated cinematic still: gentle pan, tilt, push-in, pull-back, or drift.

## Guided setup

No shell commands, paths, environment variables, Homebrew installation, or
manual model downloads are required.

1. Open **Settings → AI Defaults → Video Generation**.
2. Make sure the existing Image Default and Utility Model rows are ready.
3. Select **Install local depth engine**. This downloads the small
`depth-anything/Depth-Anything-V2-Small-hf` checkpoint and provisions FFmpeg
through `imageio-ffmpeg`. It deliberately does **not** upgrade shared Torch,
Transformers, or Diffusers packages.
4. Select **Run guided test**. This runs the same image-anchor → depth-parallax
path and writes the result to Gallery.
5. Enable generation. The direct UI and Built-in Agent Tools **Generate video**
switch use the same durable owner-scoped jobs.

### Local Image Default repair

The Image Default checklist probes the local Diffusers import boundary before
any anchor job starts. If Cookbook’s local image service has an incompatible
Diffusers/Transformers combination, choose **Repair local Image Default** in
the Video Generation card. Argos restores a v4-compatible Transformers set
without replacing the host’s Apple-Silicon PyTorch wheel. When it completes,
open Cookbook and restart the Local Diffusers server, then rerun the guided
test. No terminal commands are needed.

## Fixed output profile

- anchor-first only
- 512 × 512
- 10 seconds
- 24 FPS final output
- 6 FPS depth-parallax source frames, interpolated locally
- muted MP4
- serial jobs
- deterministic seed option

## Limits

This is not a substitute for a generative video model. It preserves the anchor
and creates camera-like depth movement; it will not reliably animate complex
body motion, speech, physics, scene changes, or objects entering frame.
