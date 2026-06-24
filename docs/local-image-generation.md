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

## Install the optional runtime

In the same virtual environment used to run the server, install:

```bash
uv pip install 'diffusers[torch]' transformers accelerate safetensors
```

The Cookbook Dependencies tab should use this exact optional package set. The
main Argos `requirements.txt` intentionally does not include it.

## Start the local server

From the repository root:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=1 \
python -m src.local_image_server \
  --profile sd-turbo \
  --served-model-id sd-turbo \
  --device auto \
  --host 127.0.0.1 \
  --port 7861 \
  --max-size 512
```

The first startup downloads the selected model to the Hugging Face cache. The
server binds to loopback only. It refuses public bind addresses by design.

Confirm that it is running:

```bash
curl http://127.0.0.1:7861/health
curl http://127.0.0.1:7861/v1/models
```

## Connect it to Argos

Add an enabled endpoint in **Settings → Services → Add Models**:

- **Name:** `Local Diffusers Image`
- **Base URL:** `http://127.0.0.1:7861/v1`
- **Type:** `Image`
- **API key:** leave blank

Argos discovers the exposed model from `/v1/models` and generates through:

```text
POST /v1/images/generations
```

The server returns OpenAI-compatible `b64_json` PNG data, which Argos already
persists in Gallery.

## Advanced SD 1.5 profile

`sd15-lcm` is an advanced profile for a compatible, accessible SD 1.5 model:

```bash
python -m src.local_image_server \
  --profile sd15-lcm \
  --model-repo <your-accessible-sd15-compatible-repository> \
  --served-model-id local-sd15-lcm \
  --device auto
```

Use 512×512 and low/medium quality first. The server serializes requests so
concurrent generations do not load multiple pipelines into 8 GB memory.

## Troubleshooting

- **`Diffusers runtime is not installed`**: install the optional dependencies
  into the server's virtual environment.
- **MPS unavailable**: use `--device auto`; it falls back to CUDA or CPU.
  CPU is supported but slow.
- **Model download/authentication failure**: choose an accessible Hugging Face
  repository, or configure the token in the environment used by the server.
- **Image too large**: lower it to 512×512, or explicitly raise `--max-size`
  only after confirming your hardware can handle it.
