# Anchor-first video generation

Argos creates a stable, muted video by first generating a Gallery image anchor,
then animating that exact image through local `mlx-video` and LTX-2.

## Runtime and model

`mlx-video` is the Apple-Silicon inference runtime. It is not itself the video
model. This integration uses the pre-converted **`prince-canuma/LTX-2-distilled`**
checkpoint, selected for the fixed local image-to-video profile.

The Video Generation setup card manages both pieces as one operation:

- installs the `mlx-video` runtime into Argos's active Python environment;
- downloads and resumes the selected LTX-2 model into Argos-managed storage;
- records the verified local snapshot path; and
- passes that local path to every video job.

This removes an implicit first-generation weight download and makes readiness
visible before a user can start a test.

## Flow

1. The user writes a high-level video intent.
2. The configured **Utility Model** derives a detailed still-image art-direction
   prompt. The original intent is not sent to image generation.
3. Argos uses the current user's **Image Default**. This can be the local
   Diffusers server configured through Cookbook or any compatible image endpoint.
4. The anchor is persisted to generated media and Gallery. The job reports
   `anchor_ready`, so both the direct UI and agent chat show the keyframe while
   animation continues.
5. The Utility Model derives a separate motion/camera prompt. The original intent
   is not sent to `mlx-video`.
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

## Guided setup

No terminal commands, environment variables, or hand-written scripts are needed.

1. Open **Settings → AI Defaults → Video Generation**.
2. In the **Image anchor** row, use the built-in endpoint/model selector. For a
   local anchor generator, choose **Open Cookbook**, start the local Diffusers
   image server, then select it as the Image Default.
3. Confirm the existing **Utility Model** or Default Chat Model is available for
   prompt planning.
4. Select **Install video engine**. Argos installs the runtime and downloads the
   selected LTX-2 checkpoint; the card reports the install or model-download
   stage and exposes safe diagnostic details on failure.
5. Select **Run guided test**. The test follows the complete image-anchor → video
   path and places its outputs in Gallery.
6. Enable video generation. The same card supports direct generation; the
   **Generate video** switch in Built-in Agent Tools controls agent use.

The setup card shows actionable state for every prerequisite and exposes installer
details only when a recovery diagnosis is needed.

## Cookbook boundary

The image anchor follows the existing Cookbook-managed image-server pattern.
`mlx-video` upstream is a local CLI/runtime rather than an OpenAI-compatible
server, so Argos's durable job worker invokes it directly. Cookbook should own
runtime/model lifecycle and diagnostics; a persistent warm LTX worker is a
future optimization, not a prerequisite for correct video generation.

## Privacy and reproducibility

Jobs keep the source intent for audit, but only the planner sees it. The image
endpoint receives the derived anchor prompt; LTX receives the separate derived
motion prompt plus a private job-local copy of the Gallery anchor. Gallery video
metadata records the anchor, derived prompts, planner model, seed, and fixed
output profile.
