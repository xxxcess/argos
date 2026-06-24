# Local image generation with Diffusers

Argos can call an OpenAI-compatible image endpoint. This repository includes an
optional local server for small Diffusers models: `src.local_image_server`.
The feature is deliberately not a core dependency, so normal Argos installs do
not download PyTorch or Diffusers.

## Recommended Mac mini M2 / 8 GB setup

Start with **SD Turbo** at **512×512**:

- profile: `sd-turbo`
- device: `mps` (or `auto`)
- resolution: `512x512`
- batch: `1`
- steps: `1`

Do not begin with SDXL, SD 3.5, or FLUX on an 8 GB unified-memory Mac. They are
substantially more likely to cause memory pressure or long CPU fallbacks.

## Fully integrated UI flow

1. Open **Cookbook → Serve**.
2. Use the **Diffusers Image Server** card.
3. Click **Install runtime**. This creates a normal Cookbook task that installs
   `diffusers[torch]`, `transformers`, `accelerate`, and `safetensors`; monitor it
   in **Cookbook → Running**.
4. Keep **SD Turbo — recommended** selected and click **Start local image server**.
5. Cookbook starts the process on loopback, converts its discovered endpoint to
   an enabled **Image** endpoint, and saves it as your Image Default.
6. Open **Settings → AI Defaults → Image Generation** to confirm
   **Local Diffusers Image / local-sd-turbo** is selected.
7. Ask normal chat to generate an image. A selected local Image Default wins over
   a tool call that names `gpt-image-1` or `dall-e-2`.

The first startup downloads the model into the Hugging Face cache. The server
binds to loopback only, and its task remains visible/manageable through
Cookbook.

## Manual server operation

The UI is the recommended route. For development or headless operation, start
it from the repository root:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=1 \
python -m src.local_image_server \
  --profile sd-turbo \
  --served-model-id local-sd-turbo \
  --device auto \
  --host 127.0.0.1 \
  --port 7861 \
  --max-size 512
```

Confirm that it is running:

```bash
curl http://127.0.0.1:7861/health
curl http://127.0.0.1:7861/v1/models
```

When registering a manually started server, add an enabled endpoint in
**Settings → Services → Add Models**:

- **Name:** `Local Diffusers Image`
- **Base URL:** `http://127.0.0.1:7861/v1`
- **Type:** `Image`
- **API key:** leave blank

Argos discovers the exposed model from `/v1/models` and generates through:

```text
POST /v1/images/generations
```

The server returns OpenAI-compatible `b64_json` PNG data, which Argos persists
in Gallery.

## Advanced SD 1.5-compatible profile

`sd15-lcm` is an advanced low-resolution profile for an accessible SD 1.5
compatible repository. It is not the recommended first choice on 8 GB; use SD
Turbo first and keep this profile at 512×512.

```bash
python -m src.local_image_server \
  --profile sd15-lcm \
  --model-repo <your-accessible-sd15-compatible-repository> \
  --served-model-id local-sd15-lcm \
  --device auto
```

The server serializes requests so concurrent generations do not load multiple
pipelines into 8 GB memory.

## Troubleshooting

- **`Diffusers runtime is not installed`**: use **Cookbook → Serve → Install runtime**.
- **MPS unavailable**: use `--device auto`; it falls back to CUDA or CPU.
  CPU is supported but slow.
- **Model download/authentication failure**: choose an accessible Hugging Face
  repository, or configure the token in the environment used by the server.
- **Image too large**: the integrated `local-*` defaults clamp normal tool calls
  to 512×512. Raise `--max-size` only after confirming your hardware can handle it.
