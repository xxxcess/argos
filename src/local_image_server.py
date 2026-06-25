"""Optional OpenAI-compatible local Diffusers image service.

This module deliberately keeps Diffusers and PyTorch optional. Argos can start
without image-generation dependencies installed; users install them only in the
Cookbook environment that launches this service.

Run:
    python -m src.local_image_server --profile sd-turbo --host 127.0.0.1 --port 7861

The service implements the small OpenAI-compatible surface used by Argos:
``GET /health``, ``GET /v1/models`` and ``POST /v1/images/generations``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import logging
import os
import platform
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_SIZE_RE = re.compile(r"^(?P<width>\d{2,5})x(?P<height>\d{2,5})$")
_PRECISIONS = {"auto", "float32", "float16"}


@dataclass(frozen=True)
class ImageProfile:
    """Conservative defaults for a locally-served image model."""

    name: str
    repo_id: str
    default_steps: int
    guidance_scale: float
    max_size: int = 512


PROFILES: dict[str, ImageProfile] = {
    "sd-turbo": ImageProfile(
        name="sd-turbo",
        repo_id="stabilityai/sd-turbo",
        default_steps=1,
        guidance_scale=0.0,
        max_size=512,
    ),
    # The profile intentionally permits a custom SD 1.5-compatible repository.
    # A LoRA is not bundled, so this remains an advanced option.
    "sd15-lcm": ImageProfile(
        name="sd15-lcm",
        repo_id="runwayml/stable-diffusion-v1-5",
        default_steps=4,
        guidance_scale=1.0,
        max_size=512,
    ),
}


class ImageGenerationRequest(BaseModel):
    model: str = ""
    prompt: str = Field(min_length=1, max_length=8000)
    n: int = 1
    size: str = "512x512"
    quality: str = "medium"


def _choose_device(requested: str) -> str:
    """Return the requested device when viable, otherwise the safest option."""

    try:
        import torch
    except ImportError as exc:  # pragma: no cover - startup error path
        raise RuntimeError(
            "PyTorch is not installed. Open Cookbook → Dependencies and install "
            "the Diffusers runtime (diffusers[torch], transformers, accelerate, safetensors)."
        ) from exc

    choice = (requested or "auto").strip().lower()
    if choice not in {"auto", "mps", "cuda", "cpu"}:
        raise RuntimeError("device must be auto, mps, cuda, or cpu")
    if choice == "mps":
        if not getattr(torch.backends, "mps", None) or not torch.backends.mps.is_available():
            raise RuntimeError("MPS was requested but is not available in this PyTorch installation")
        return "mps"
    if choice == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        return "cuda"
    if choice == "cpu":
        return "cpu"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _resolve_dtype(torch: Any, device: str, precision: str) -> tuple[Any, str, bool]:
    """Select a stable dtype and whether a fp16 repository variant is safe.

    SD Turbo + MPS fp16 can emit NaN image tensors on some Apple Silicon/PyTorch
    combinations. Diffusers then casts those NaNs to zero and produces a black
    PNG. For MPS, `auto` deliberately uses float32 and the default model weights.
    CUDA retains the lightweight fp16 default. Users can still opt into float16
    explicitly for experimentation, but the service health output records it.
    """

    selected = (precision or "auto").strip().lower()
    if selected not in _PRECISIONS:
        raise RuntimeError("precision must be auto, float32, or float16")
    if selected == "float32":
        return torch.float32, "float32", False
    if selected == "float16":
        return torch.float16, "float16", device == "cuda"
    if device == "cuda":
        return torch.float16, "float16", True
    # MPS is intentionally float32 by default for output correctness.
    return torch.float32, "float32", False


def _parse_size(value: str, max_size: int) -> tuple[int, int]:
    match = _SIZE_RE.match((value or "").strip())
    if not match:
        raise ValueError("size must be formatted as WIDTHxHEIGHT, for example 512x512")
    width, height = int(match.group("width")), int(match.group("height"))
    if width < 64 or height < 64:
        raise ValueError("minimum image size is 64x64")
    if width > max_size or height > max_size:
        raise ValueError(f"this local profile supports up to {max_size}x{max_size}")
    if width % 8 or height % 8:
        raise ValueError("width and height must be multiples of 8")
    return width, height


def _steps_for_quality(profile: ImageProfile, quality: str) -> int:
    quality = (quality or "medium").lower()
    if profile.name == "sd-turbo":
        # SD Turbo was trained for one-step sampling. Extra steps waste memory
        # and do not improve this low-memory profile.
        return 1
    return {"low": 2, "medium": profile.default_steps, "high": 8, "auto": profile.default_steps}.get(
        quality, profile.default_steps
    )


def _assert_not_blank(image: Any) -> None:
    """Reject the all-black frames observed in failed MPS/model loads.

    This intentionally catches only near-zero pixels. A legitimate dark image
    with any visible range remains valid; the aim is to prevent a completely
    black 512×512 PNG from being saved to Gallery as a successful generation.
    """

    try:
        preview = image.convert("RGB").resize((32, 32))
        extrema = preview.getextrema()
        if all(high <= 1 for _low, high in extrema):
            raise RuntimeError(
                "Local Diffusers produced a blank black image. The MPS runtime is unstable for this model/configuration; "
                "restart with float32 precision and keep the request at 512x512."
            )
    except AttributeError:
        # A custom pipeline returned a non-PIL image; let the normal save path
        # produce the more useful error in that uncommon case.
        return


class LocalImageService:
    """Holds one lazily-loaded pipeline and serializes inference requests."""

    def __init__(
        self,
        profile: ImageProfile,
        served_model_id: str,
        device: str = "auto",
        precision: str = "auto",
        max_size: Optional[int] = None,
        pipeline_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.profile = profile
        self.served_model_id = served_model_id or profile.name
        self.requested_device = device
        self.requested_precision = precision
        self.precision = ""
        self.max_size = max_size or profile.max_size
        self.pipeline_factory = pipeline_factory
        self.pipeline: Any = None
        self.device = ""
        self.status = "starting"
        self.error = ""
        self._load_lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()

    async def load(self) -> None:
        if self.pipeline is not None:
            return
        async with self._load_lock:
            if self.pipeline is not None:
                return
            self.status = "loading"
            try:
                await asyncio.to_thread(self._load_sync)
                self.status = "ready"
                self.error = ""
            except Exception as exc:
                self.status = "error"
                self.error = str(exc)
                logger.exception("Local image pipeline failed to load")
                raise

    def _load_sync(self) -> None:
        try:
            import torch
            from diffusers import AutoPipelineForText2Image
        except ImportError as exc:
            raise RuntimeError(
                "Diffusers runtime is not installed. Open Cookbook → Dependencies and install "
                "diffusers[torch], transformers, accelerate, and safetensors."
            ) from exc

        self.device = _choose_device(self.requested_device)
        dtype, self.precision, use_fp16_variant = _resolve_dtype(
            torch, self.device, self.requested_precision
        )
        factory = self.pipeline_factory or AutoPipelineForText2Image.from_pretrained
        load_kwargs: dict[str, Any] = {"torch_dtype": dtype, "use_safetensors": True}

        # Only CUDA gets the fp16 variant automatically. For Apple MPS the
        # float32 default is intentional: it avoids NaN-to-black output seen in
        # the Cookbook logs for SD Turbo.
        if use_fp16_variant:
            load_kwargs["variant"] = "fp16"
        try:
            pipe = factory(self.profile.repo_id, **load_kwargs)
        except (OSError, ValueError) as exc:
            if "variant" not in load_kwargs:
                raise
            logger.warning("Could not load fp16 variant for %s; retrying default weights: %s", self.profile.repo_id, exc)
            load_kwargs.pop("variant", None)
            pipe = factory(self.profile.repo_id, **load_kwargs)

        # These toggles trade speed for lower peak memory, which is especially
        # important on an 8 GB Apple Silicon system. Max slicing is used on MPS
        # to make the conservative float32 default more likely to fit.
        attention_slicing = getattr(pipe, "enable_attention_slicing", None)
        if callable(attention_slicing):
            attention_slicing("max" if self.device == "mps" else "auto")
        vae_slicing = getattr(pipe, "enable_vae_slicing", None)
        if callable(vae_slicing):
            vae_slicing()
        pipe.to(self.device)
        configure = getattr(pipe, "set_progress_bar_config", None)
        if callable(configure):
            configure(disable=True)
        self.pipeline = pipe
        logger.info(
            "Loaded local image pipeline profile=%s device=%s precision=%s repo=%s",
            self.profile.name,
            self.device,
            self.precision,
            self.profile.repo_id,
        )

    async def generate(self, request: ImageGenerationRequest) -> dict[str, Any]:
        if request.n != 1:
            raise ValueError("local image generation currently supports n=1 only")
        if request.model and request.model != self.served_model_id:
            raise ValueError(
                f"model '{request.model}' is not available from this server; use '{self.served_model_id}'"
            )
        width, height = _parse_size(request.size, self.max_size)
        await self.load()
        async with self._inference_lock:
            return await asyncio.to_thread(self._generate_sync, request.prompt, width, height, request.quality)

    def _generate_sync(self, prompt: str, width: int, height: int, quality: str) -> dict[str, Any]:
        if self.pipeline is None:
            raise RuntimeError("image pipeline is unavailable")
        try:
            result = self.pipeline(
                prompt=prompt,
                num_inference_steps=_steps_for_quality(self.profile, quality),
                guidance_scale=self.profile.guidance_scale,
                width=width,
                height=height,
            )
            images = getattr(result, "images", None) or []
            if not images:
                raise RuntimeError("Diffusers returned no image")
            _assert_not_blank(images[0])
            stream = io.BytesIO()
            images[0].save(stream, format="PNG")
            return {
                "created": int(time.time()),
                "data": [{"b64_json": base64.b64encode(stream.getvalue()).decode("ascii")}],
            }
        finally:
            # Reclaim temporary MPS allocations between serialized requests.
            if self.device == "mps":
                try:
                    import torch

                    torch.mps.empty_cache()
                except Exception:
                    pass

    def health(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "model": self.served_model_id,
            "profile": self.profile.name,
            "repo_id": self.profile.repo_id,
            "device": self.device or self.requested_device,
            "precision": self.precision or self.requested_precision,
            "max_size": self.max_size,
            "warning": "CPU fallback is active; image generation will be slow." if self.device == "cpu" else "",
            "error": self.error,
        }


def create_app(service: LocalImageService) -> FastAPI:
    app = FastAPI(title="Argos Local Diffusers Image Server", version="1.0")

    @app.on_event("startup")
    async def _announce_startup() -> None:
        # Do not block HTTP startup while the first model download is running.
        # `/v1/models` can be discovered immediately; the first generate call
        # performs the lazy load and returns an actionable failure if it cannot.
        service.status = "starting"

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return service.health()

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": service.served_model_id, "object": "model"}]}

    @app.post("/v1/images/generations")
    async def generate(payload: ImageGenerationRequest) -> dict[str, Any]:
        try:
            return await service.generate(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Local image generation failed")
            raise HTTPException(status_code=500, detail=f"local image generation failed: {exc}") from exc

    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve a local Diffusers image model for Argos")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="sd-turbo")
    parser.add_argument("--model-repo", default="", help="Override the Hugging Face repository for this profile")
    parser.add_argument("--served-model-id", default="", help="Model id exposed from /v1/models")
    parser.add_argument("--device", choices=("auto", "mps", "cuda", "cpu"), default="auto")
    parser.add_argument("--precision", choices=sorted(_PRECISIONS), default="auto")
    parser.add_argument("--max-size", type=int, default=512, help="Maximum width/height (default: 512)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback only)")
    parser.add_argument("--port", type=int, default=7861)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("For safety, local image server binding is restricted to 127.0.0.1, ::1, or localhost")
    if args.max_size < 64 or args.max_size > 2048 or args.max_size % 8:
        raise SystemExit("--max-size must be a multiple of 8 between 64 and 2048")
    if platform.system() == "Darwin" and args.device in {"auto", "mps"}:
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    profile = PROFILES[args.profile]
    if args.model_repo:
        profile = ImageProfile(
            name=profile.name,
            repo_id=args.model_repo,
            default_steps=profile.default_steps,
            guidance_scale=profile.guidance_scale,
            max_size=profile.max_size,
        )
    service = LocalImageService(
        profile=profile,
        served_model_id=args.served_model_id or profile.name,
        device=args.device,
        precision=args.precision,
        max_size=args.max_size,
    )
    import uvicorn

    uvicorn.run(create_app(service), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
