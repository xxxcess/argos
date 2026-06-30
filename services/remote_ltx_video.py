"""Remote LTX Video provider backed by the public Hugging Face Space.

The public Space is intentionally treated as an unstable third-party surface:
health checks inspect API metadata only, render jobs validate the live endpoint
shape before upload, and all remote errors are reduced to bounded user-safe
messages.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from services.depth_parallax_renderer import RenderCancelled
from src.anchor_video_settings import REMOTE_LTX_CONSENT_VERSION, has_remote_ltx_consent

SPACE_ID = "Lightricks/ltx-video-distilled"
PROVIDER_ID = "remote_ltx"
PROVIDER_LABEL = "Remote LTX"
API_ENDPOINT = f"https://huggingface.co/spaces/{SPACE_ID}"
TARGET_DURATION_SECONDS = 8.0
TARGET_FPS = 30
MAX_REMOTE_VIDEO_BYTES = 220 * 1024 * 1024
DEFAULT_NEGATIVE_PROMPT = (
    "low quality, distorted anatomy, warped limbs, duplicate subjects, text, watermark, logo, "
    "jitter, flicker, abrupt cuts, scene change"
)


class RemoteLtxError(RuntimeError):
    pass


class RemoteLtxCompatibilityError(RemoteLtxError):
    pass


@dataclass(frozen=True)
class RemoteLtxEndpointSignature:
    api_name: str
    prompt_param: str
    image_param: str
    image_transport: str
    negative_prompt_param: str | None = None
    seed_param: str | None = None
    duration_param: str | None = None
    width_param: str | None = None
    height_param: str | None = None
    fps_param: str | None = None
    mode_param: str | None = None
    randomize_seed_param: str | None = None
    frames_to_use_param: str | None = None
    guidance_scale_param: str | None = None
    improve_texture_param: str | None = None


@dataclass(frozen=True)
class RemoteLtxRenderedVideo:
    path: Path
    actual_frame_count: int
    actual_fps: float
    actual_duration_seconds: float
    generation_params: dict[str, Any]


_HEALTH_CACHE: dict[str, Any] = {"checked_at": 0.0, "status": None}
_HEALTH_TTL_SECONDS = 300.0


def _safe_error(error: Exception | str) -> str:
    text = re.sub(r"\s+", " ", str(error or "Remote LTX Video failed.")).strip()
    text = re.sub(r"https?://\S+", "[remote-url]", text)
    return (text or "Remote LTX Video failed.")[:360]


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _component_text(param: dict[str, Any]) -> str:
    fields = [
        param.get("parameter_name"),
        param.get("name"),
        param.get("label"),
        param.get("component"),
        param.get("type"),
        param.get("python_type"),
        param.get("description"),
        param.get("api_info"),
    ]
    return _normalize(" ".join(str(field) for field in fields if field is not None))


def _param_name(param: dict[str, Any]) -> str:
    return str(
        param.get("parameter_name")
        or param.get("name")
        or param.get("label")
        or ""
    ).strip()


def _endpoint_items(api_info: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for key in ("named_endpoints", "unnamed_endpoints", "endpoints"):
        value = api_info.get(key)
        if isinstance(value, dict):
            for name, spec in value.items():
                if isinstance(spec, dict):
                    items.append((str(name), spec))
        elif isinstance(value, list):
            for index, spec in enumerate(value):
                if isinstance(spec, dict):
                    items.append((str(spec.get("api_name") or spec.get("name") or f"/predict_{index}"), spec))
    if not items and any(key in api_info for key in ("parameters", "params")):
        items.append((str(api_info.get("api_name") or "/predict"), api_info))
    return items


def _parameters(spec: dict[str, Any]) -> list[dict[str, Any]]:
    params = spec.get("parameters") or spec.get("params") or []
    if isinstance(params, dict):
        params = list(params.values())
    return [param for param in params if isinstance(param, dict)]


def inspect_remote_ltx_schema(api_info: dict[str, Any]) -> RemoteLtxEndpointSignature:
    """Return a compatible image-to-video endpoint or raise.

    The public Space may change at any time. A compatible endpoint must accept
    at least an image file and a motion prompt. Other controls are optional and
    are supplied only when the live schema exposes them.
    """
    candidates: list[tuple[int, str, dict[str, Any], list[dict[str, Any]]]] = []
    for api_name, spec in _endpoint_items(api_info):
        params = _parameters(spec)
        joined = _normalize(api_name + " " + str(spec))
        has_image_to_video_hint = (
            "image to video" in joined
            or "image video" in joined
            or "i2v" in joined
            or ("image" in joined and "video" in joined)
        )
        if not has_image_to_video_hint and len(_endpoint_items(api_info)) > 1:
            continue
        score = 3 if has_image_to_video_hint else 1
        candidates.append((score, api_name, spec, params))

    compatible: list[tuple[int, RemoteLtxEndpointSignature]] = []
    for score, api_name, _spec, params in sorted(candidates, key=lambda item: item[0], reverse=True):
        prompt_param = image_param = image_transport = None
        negative_param = seed_param = duration_param = width_param = height_param = fps_param = mode_param = randomize_seed_param = None
        frames_to_use_param = guidance_scale_param = improve_texture_param = None
        for param in params:
            name = _param_name(param)
            text = _component_text(param)
            if not name:
                continue
            if image_param is None and ("image" in text or "filepath" in text or "file" in text):
                image_param = name
                image_transport = "uploaded_path" if ("textbox" in text or "str" in text) else "file_data"
                continue
            if prompt_param is None and "prompt" in text and "negative" not in text:
                prompt_param = name
                continue
            if negative_param is None and "negative" in text and "prompt" in text:
                negative_param = name
                continue
            if randomize_seed_param is None and "random" in text and "seed" in text:
                randomize_seed_param = name
                continue
            if seed_param is None and "seed" in text:
                seed_param = name
                continue
            if duration_param is None and ("duration" in text or "seconds" in text):
                duration_param = name
                continue
            if frames_to_use_param is None and "frames" in text and "use" in text:
                frames_to_use_param = name
                continue
            if width_param is None and "width" in text:
                width_param = name
                continue
            if height_param is None and "height" in text:
                height_param = name
                continue
            if fps_param is None and ("fps" in text or "frames per second" in text):
                fps_param = name
                continue
            if guidance_scale_param is None and ("guidance" in text or "cfg" in text):
                guidance_scale_param = name
                continue
            if improve_texture_param is None and "improve" in text and "texture" in text:
                improve_texture_param = name
                continue
            if mode_param is None and ("mode" in text or "task" in text):
                mode_param = name
        if prompt_param and image_param and image_transport:
            # The public LTX Space currently exposes /image_to_video as an Image
            # component but its backend expects a filepath string. Prefer the
            # endpoint that accepts a string filepath plus mode=image-to-video.
            transport_score = 4 if image_transport == "uploaded_path" else 0
            mode_score = 2 if mode_param else 0
            name_score = 1 if "image_to_video" in api_name else 0
            compatible.append((
                score + transport_score + mode_score + name_score,
                RemoteLtxEndpointSignature(
                    api_name=api_name,
                    prompt_param=prompt_param,
                    image_param=image_param,
                    image_transport=image_transport,
                    negative_prompt_param=negative_param,
                    seed_param=seed_param,
                    duration_param=duration_param,
                    width_param=width_param,
                    height_param=height_param,
                    fps_param=fps_param,
                    mode_param=mode_param,
                    randomize_seed_param=randomize_seed_param,
                    frames_to_use_param=frames_to_use_param,
                    guidance_scale_param=guidance_scale_param,
                    improve_texture_param=improve_texture_param,
                ),
            ))
    if compatible:
        return sorted(compatible, key=lambda item: item[0], reverse=True)[0][1]
    raise RemoteLtxCompatibilityError("Remote LTX interface changed; this provider has been disabled until compatibility is updated.")


def _client(download_dir: Path | None = None):
    try:
        from gradio_client import Client
    except Exception as exc:
        raise RemoteLtxError("Remote LTX client dependency is not installed.") from exc
    token = os.getenv("ARGOS_REMOTE_LTX_HF_TOKEN") or None
    kwargs: dict[str, Any] = {
        "verbose": False,
        "analytics_enabled": False,
        "httpx_kwargs": {"timeout": 60.0},
    }
    if download_dir is not None:
        kwargs["download_files"] = str(download_dir)
    if token:
        kwargs["token"] = token
    try:
        return Client(SPACE_ID, **kwargs)
    except TypeError:
        # Older gradio_client used hf_token instead of token.
        if token:
            kwargs.pop("token", None)
            kwargs["hf_token"] = token
        return Client(SPACE_ID, **kwargs)


def _view_api_dict(client: Any) -> dict[str, Any]:
    info = client.view_api(return_format="dict")
    if not isinstance(info, dict):
        raise RemoteLtxCompatibilityError("Remote LTX interface metadata is unavailable.")
    return info


def remote_ltx_status(*, force: bool = False, client_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    now = time.monotonic()
    if not force and _HEALTH_CACHE["status"] is not None and now - float(_HEALTH_CACHE["checked_at"]) < _HEALTH_TTL_SECONDS:
        return dict(_HEALTH_CACHE["status"])
    try:
        client = client_factory() if client_factory else _client()
        signature = inspect_remote_ltx_schema(_view_api_dict(client))
        status = {
            "available": True,
            "provider": PROVIDER_ID,
            "space_id": SPACE_ID,
            "api_endpoint": API_ENDPOINT,
            "api_name": signature.api_name,
            "message": "Remote LTX available. Public shared queue.",
        }
    except RemoteLtxCompatibilityError as exc:
        status = {
            "available": False,
            "provider": PROVIDER_ID,
            "space_id": SPACE_ID,
            "api_endpoint": API_ENDPOINT,
            "message": _safe_error(exc),
            "reason": _safe_error(exc),
        }
    except Exception as exc:
        status = {
            "available": False,
            "provider": PROVIDER_ID,
            "space_id": SPACE_ID,
            "api_endpoint": API_ENDPOINT,
            "message": "Remote LTX is temporarily unavailable.",
            "reason": _safe_error(exc),
        }
    _HEALTH_CACHE.update({"checked_at": now, "status": status})
    return dict(status)


def _upload_anchor_path(client: Any, anchor_path: Path) -> str:
    try:
        with anchor_path.open("rb") as handle:
            response = httpx.post(
                client.upload_url,
                headers=getattr(client, "headers", None),
                cookies=getattr(client, "cookies", None),
                verify=getattr(client, "ssl_verify", True),
                files=[("files", (anchor_path.name, handle))],
                **getattr(client, "httpx_kwargs", {}),
            )
        response.raise_for_status()
        uploaded = response.json()
        if isinstance(uploaded, list) and uploaded and isinstance(uploaded[0], str):
            return uploaded[0]
    except Exception as exc:
        raise RemoteLtxError("Remote LTX anchor upload failed.") from exc
    raise RemoteLtxError("Remote LTX anchor upload failed.")


def _submit_kwargs(client: Any, signature: RemoteLtxEndpointSignature, anchor_path: Path, prompt: str, seed: int) -> dict[str, Any]:
    if signature.image_transport == "file_data":
        from gradio_client import handle_file
        image_value: Any = handle_file(str(anchor_path))
    else:
        image_value = _upload_anchor_path(client, anchor_path)

    kwargs: dict[str, Any] = {
        signature.prompt_param: prompt,
        signature.image_param: image_value,
    }
    if signature.negative_prompt_param:
        kwargs[signature.negative_prompt_param] = DEFAULT_NEGATIVE_PROMPT
    if signature.seed_param:
        kwargs[signature.seed_param] = int(seed)
    if signature.randomize_seed_param:
        kwargs[signature.randomize_seed_param] = False
    if signature.duration_param:
        kwargs[signature.duration_param] = TARGET_DURATION_SECONDS
    if signature.frames_to_use_param:
        kwargs[signature.frames_to_use_param] = 9
    if signature.width_param:
        kwargs[signature.width_param] = 512
    if signature.height_param:
        kwargs[signature.height_param] = 512
    if signature.fps_param:
        kwargs[signature.fps_param] = TARGET_FPS
    if signature.mode_param:
        kwargs[signature.mode_param] = "image-to-video"
    if signature.guidance_scale_param:
        kwargs[signature.guidance_scale_param] = 1.0
    if signature.improve_texture_param:
        kwargs[signature.improve_texture_param] = False
    return kwargs


def _status_code(job_status: Any) -> str:
    code = getattr(job_status, "code", job_status)
    value = getattr(code, "value", code)
    return str(value or "").lower()


def _stage_from_status(job_status: Any) -> str:
    code = _status_code(job_status)
    if any(token in code for token in ("queue", "queued", "rank", "waiting", "starting")):
        return "waiting_remote_queue"
    return "generating_remote_ltx"


def _iter_result_values(value: Any):
    if value is None:
        return
    if isinstance(value, (str, os.PathLike)):
        yield value
    elif isinstance(value, dict):
        for key in ("path", "url", "video", "file", "name"):
            if key in value:
                yield from _iter_result_values(value[key])
        for item in value.values():
            if isinstance(item, (dict, list, tuple)):
                yield from _iter_result_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_result_values(item)


def _is_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _download_url(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=180.0) as response:
            if response.status_code >= 400:
                raise RemoteLtxError("Remote LTX result download failed.")
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].lower()
            if content_type and content_type not in {"video/mp4", "application/octet-stream", "binary/octet-stream"}:
                raise RemoteLtxError("Remote LTX returned an unsupported video response.")
            total = 0
            with target.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 256):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_REMOTE_VIDEO_BYTES:
                        raise RemoteLtxError("Remote LTX video exceeded the allowed download size.")
                    handle.write(chunk)
    except RemoteLtxError:
        raise
    except Exception as exc:
        raise RemoteLtxError("Remote LTX result download failed.") from exc
    return target


def _local_result_path(result: Any, work_dir: Path) -> Path:
    for value in _iter_result_values(result):
        raw = str(value)
        if _is_url(raw):
            return _download_url(raw, work_dir / "remote-ltx-result.mp4")
        path = Path(raw)
        if path.is_file():
            return path
    raise RemoteLtxError("Remote LTX did not return a video file.")


def parse_mp4_metadata(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 1024:
        raise RemoteLtxError("Remote LTX returned an empty video.")
    if b"ftyp" not in data[:32] or b"moov" not in data or b"mdat" not in data:
        raise RemoteLtxError("Remote LTX returned an invalid MP4.")

    def _box_children(start: int, end: int, path_names: list[str]) -> list[tuple[int, int]]:
        import struct

        if not path_names:
            return [(start, end)]
        out: list[tuple[int, int]] = []
        offset = start
        while offset + 8 <= end:
            size = struct.unpack(">I", data[offset:offset + 4])[0]
            box_type = data[offset + 4:offset + 8].decode("latin1")
            header = 8
            if size == 1:
                size = struct.unpack(">Q", data[offset + 8:offset + 16])[0]
                header = 16
            if size == 0:
                size = end - offset
            if size < header:
                break
            if box_type == path_names[0]:
                if len(path_names) == 1:
                    out.append((offset + header, offset + size))
                else:
                    out.extend(_box_children(offset + header, offset + size, path_names[1:]))
            offset += size
        return out

    try:
        import struct

        duration = 0.0
        for start, _end in _box_children(0, len(data), ["moov", "mvhd"]):
            version = data[start]
            cursor = start + 4
            if version == 1:
                _creation, _modified, timescale, raw_duration = struct.unpack(">QQIQ", data[cursor:cursor + 28])
            else:
                _creation, _modified, timescale, raw_duration = struct.unpack(">IIII", data[cursor:cursor + 16])
            if timescale:
                duration = max(duration, float(raw_duration) / float(timescale))
        frame_count = 0
        for start, _end in _box_children(0, len(data), ["moov", "trak", "mdia", "minf", "stbl", "stsz"]):
            cursor = start + 4
            if cursor + 8 <= len(data):
                _sample_size, count = struct.unpack(">II", data[cursor:cursor + 8])
                frame_count = max(frame_count, int(count))
    except Exception as exc:
        raise RemoteLtxError("Remote LTX returned an invalid MP4.") from exc
    if duration <= 0 or frame_count <= 0:
        raise RemoteLtxError("Remote LTX returned an MP4 without playable timing metadata.")
    return {
        "actual_duration_seconds": duration,
        "actual_frame_count": frame_count,
        "actual_fps": float(frame_count) / duration,
        "file_size": path.stat().st_size,
    }


def validate_remote_video(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RemoteLtxError("Remote LTX returned no video file.")
    size = path.stat().st_size
    if size <= 0:
        raise RemoteLtxError("Remote LTX returned an empty video.")
    if size > MAX_REMOTE_VIDEO_BYTES:
        raise RemoteLtxError("Remote LTX video exceeded the allowed download size.")
    return parse_mp4_metadata(path)


class RemoteLtxProvider:
    provider_id = PROVIDER_ID

    def status(self) -> dict[str, Any]:
        return remote_ltx_status()

    def validate_request(self, config: dict[str, Any]) -> None:
        if not has_remote_ltx_consent(config):
            raise RemoteLtxError("Remote LTX Video requires public-provider acknowledgement before use.")
        status = remote_ltx_status()
        if not status.get("available"):
            raise RemoteLtxError(status.get("reason") or status.get("message") or "Remote LTX is temporarily unavailable.")

    def render(
        self,
        *,
        anchor_path: Path,
        motion_prompt: str,
        seed: int,
        progress_callback: Callable[[str], None],
        cancelled: Callable[[], bool],
        work_dir: Path,
    ) -> RemoteLtxRenderedVideo:
        if cancelled():
            raise RenderCancelled("Video generation was cancelled.")
        progress_callback("submitting_remote_ltx")
        client = _client(work_dir)
        signature = inspect_remote_ltx_schema(_view_api_dict(client))
        try:
            job = client.submit(
                api_name=signature.api_name,
                **_submit_kwargs(client, signature, anchor_path, motion_prompt, seed),
            )
        except Exception as exc:
            message = _safe_error(exc)
            if "rate" in message.lower() or "429" in message:
                raise RemoteLtxError("Remote LTX request was rate-limited. Retry later or switch to Local Motion.") from exc
            raise RemoteLtxError("Remote LTX could not accept the request.") from exc

        while not getattr(job, "done", lambda: True)():
            if cancelled():
                cancel = getattr(job, "cancel", None)
                if callable(cancel):
                    try:
                        cancel()
                    except Exception:
                        pass
                raise RenderCancelled("Video generation was cancelled.")
            try:
                progress_callback(_stage_from_status(job.status()))
            except Exception:
                progress_callback("waiting_remote_queue")
            time.sleep(2.0)

        if cancelled():
            raise RenderCancelled("Video generation was cancelled.")
        progress_callback("generating_remote_ltx")
        try:
            result = job.result()
        except Exception as exc:
            message = _safe_error(exc)
            if "rate" in message.lower() or "429" in message:
                raise RemoteLtxError("Remote LTX request was rate-limited. Retry later or switch to Local Motion.") from exc
            raise RemoteLtxError("Remote LTX generation failed.") from exc
        if cancelled():
            raise RenderCancelled("Video generation was cancelled.")

        progress_callback("downloading_remote_video")
        source = _local_result_path(result, work_dir)
        candidate = work_dir / "remote-ltx-validated.mp4"
        if source.resolve() != candidate.resolve():
            shutil.copy2(source, candidate)
        progress_callback("validating_remote_video")
        metadata = validate_remote_video(candidate)
        return RemoteLtxRenderedVideo(
            path=candidate,
            actual_frame_count=int(metadata["actual_frame_count"]),
            actual_fps=float(metadata["actual_fps"]),
            actual_duration_seconds=float(metadata["actual_duration_seconds"]),
            generation_params={
                "provider": PROVIDER_ID,
                "provider_label": PROVIDER_LABEL,
                "space_id": SPACE_ID,
                "api_endpoint": API_ENDPOINT,
                "api_name": signature.api_name,
                "requested_duration_seconds": TARGET_DURATION_SECONDS,
                "target_fps": TARGET_FPS,
                "seed": int(seed),
                "public_provider_consent_version": REMOTE_LTX_CONSENT_VERSION,
            },
        )
