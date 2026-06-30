# Anchor-First Video Generation

Argos creates short, muted Gallery videos from a generated image anchor. The
anchor always comes from the user's configured Image Default. The selected
render provider then turns that anchor into motion.

## Providers

### Local Motion

- private/local
- uses Depth Anything V2 Small plus deterministic depth-parallax warping
- creates subtle camera movement only: pan, tilt, push-in, pull-back, or drift
- does not promise wing flaps, walking, turning, speech, cloth simulation, fire,
  smoke, or other true subject motion
- fixed 512 x 512 output
- exact 8 seconds
- 48 source frames at 6 FPS
- 192 final frames at 24 FPS
- muted MP4

Choose Local Motion when privacy matters or when an animated cinematic still is
the right result.

### Remote LTX Video

- public/shared remote image-to-video generation
- uses the public Hugging Face Space `Lightricks/ltx-video-distilled`
- intended for action-heavy prompts such as wing flaps, walking, turning,
  flying, smoke, rain, fire, water, cloth movement, and creature motion
- target duration is about 8 seconds at about 30 FPS
- actual returned duration, FPS, and frame count are stored from the MP4
- muted MP4

Choose Remote LTX Video when the request needs actual subject or environmental
motion. It is not selected automatically. The saved provider in Settings is
used by both the direct Settings workflow and the Built-in Agent Tool.

## Privacy Disclosure

Remote LTX Video requires an explicit persisted acknowledgement before it can be
selected or used:

> Your anchor image and derived motion prompt will be uploaded to the public
> Lightricks LTX Video Hugging Face Space on shared third-party infrastructure.
> Queue time, availability, retention, and output quality are outside Argos's
> control.

The acknowledgement applies only to the named public provider version
`public-ltx-v1`. Switching back to Local Motion keeps local video generation
available and private. Health checks for Remote LTX query API metadata only and
do not upload an image or prompt.

## Data Flow

1. The user writes a high-level video intent in Settings or asks the enabled
   **Generate video** agent tool.
2. The selected Utility Model derives a still-image art direction prompt. The
   raw intent is retained for audit but is not sent unchanged to image
   generation or video rendering.
3. Argos generates a stable Gallery image anchor through the configured Image
   Default.
4. The Utility Model derives provider-specific motion guidance:
   - Local Motion: bounded camera movement only.
   - Remote LTX Video: image-to-video action prompt that preserves the anchor's
     identity, anatomy, composition, lighting, and environment.
5. Argos renders through the selected provider.
6. The validated MP4 is copied into Gallery with provider, seed, requested
   duration, actual duration, FPS, frame count, and consent-version metadata.

The agent tool still exposes one `generate_video` function. It creates one
durable video job, creates the anchor internally, and does not separately call
the standalone image-generation tool.

## Remote Availability

The public Space can change, queue, rate-limit, or disappear without notice.
Argos checks API metadata before accepting Remote LTX jobs and disables the
provider if the image-to-video interface is no longer compatible. Remote LTX
failures do not silently fall back to Local Motion; the user must retry later or
switch providers explicitly.

Remote errors shown in chat and Settings are bounded and do not expose internal
paths, remote queue URLs, credentials, installer commands, or raw HTML error
pages.

## Guided Setup

1. Open **Settings -> AI Defaults -> Video Generation**.
2. Confirm the Image Default and Utility Model rows are ready.
3. Choose a render provider:
   - **Local Motion - private** for local camera movement.
   - **Remote LTX Video - public shared GPU** for action-heavy motion, after
     acknowledging public processing.
4. For Local Motion, use **Install local depth engine** if needed. This
   provisions the small depth model and FFmpeg helper without replacing
   Apple-Silicon PyTorch wheels.
5. For Remote LTX Video, use **Check Remote LTX availability**. This checks
   schema metadata only.
6. Enable video generation.

## Limits

Local Motion preserves the image and creates camera-like depth movement. It will
not reliably animate complex body motion, speech, physics, scene changes, or
objects entering frame.

Remote LTX Video can create actual image-to-video motion, but public shared
generation has no guarantee of exact anatomy, action, temporal consistency,
availability, queue time, or output quality. It should be treated as a public
third-party generation provider.
