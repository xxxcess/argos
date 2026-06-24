# Native video generation

Argos video generation is a local, silent-MP4 feature powered by `mlx-video`
and LTX-2. It is intentionally available only on native macOS running on Apple
Silicon. Docker Desktop on macOS runs Linux in a VM and cannot use this native
MLX path.

## Install

From an Argos native macOS virtual environment:

```bash
python -m pip install -r requirements-mlx-video-macos.txt
```

The runtime command defaults to `mlx_video.ltx_2.generate`. Set
`MLX_VIDEO_BIN` only when the executable was installed under a different name
or location. The configured value must point to an executable available to the
Argos process.

## Use

Open **Settings → AI Defaults → Video Generation**, enable local generation,
and choose one of the supported LTX-2 distilled presets. The initial release
supports:

- 512×512, 768×512, and 512×768
- 33, 49, or 97 frames at 24 FPS
- random or fixed seeds
- silent video only

Each request is placed in a durable single-concurrency queue. Successful MP4s
are owner-protected and added to Gallery automatically.

## Troubleshooting

`GET /api/video/runtime` explains whether the host is supported and whether
`mlx-video` is available. On non-macOS, Intel Macs, Docker/Linux, or missing
runtime installs, video controls remain visible but generation is disabled with
a descriptive error.
