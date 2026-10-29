from dataclasses import dataclass
import math
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    camera_device: str
    camera_source: str
    camera_rtsp_url: str | None
    camera_id: str
    width: int
    height: int
    fps: float
    segment_seconds: float
    segment_retention_seconds: int
    pre_event_seconds: int
    post_event_seconds: int
    quota_bytes: int
    min_free_bytes: int
    retry_seconds: float
    max_pending_clips: int
    max_clip_bytes: int
    record_audio: bool

    @classmethod
    def from_env(cls) -> "Settings":
        def required(name: str) -> str:
            value = os.environ.get(name)
            if value is None or value == "":
                raise ValueError(f"variável obrigatória ausente: {name}")
            return value

        def boolean(name: str) -> bool:
            value = required(name).strip().lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{name} deve ser true ou false")
            return value == "true"

        settings = cls(
            data_dir=Path(required("HOST_DATA_DIR")),
            camera_device=os.environ.get("CAMERA_DEVICE_PATH", ""),
            camera_source=os.environ.get("CAMERA_SOURCE", "webcam").strip().lower(),
            camera_rtsp_url=os.environ.get("CAMERA_RTSP_URL"),
            camera_id=required("CAMERA_ID"),
            width=int(required("FRAME_WIDTH")),
            height=int(required("FRAME_HEIGHT")),
            fps=float(required("FRAME_RATE")),
            segment_seconds=float(required("SEGMENT_SECONDS")),
            segment_retention_seconds=int(required("SEGMENT_RETENTION_SECONDS")),
            pre_event_seconds=int(required("PRE_EVENT_SECONDS")),
            post_event_seconds=int(required("POST_EVENT_SECONDS")),
            quota_bytes=int(required("STORAGE_QUOTA_BYTES")),
            min_free_bytes=int(required("MIN_FREE_BYTES")),
            retry_seconds=float(required("CAPTURE_RETRY_SECONDS")),
            max_pending_clips=int(required("MAX_PENDING_CLIPS")),
            max_clip_bytes=int(required("MAX_CLIP_BYTES")),
            record_audio=boolean("RECORD_AUDIO"),
        )
        if (settings.width < 1 or settings.width > 4096 or settings.height < 1 or settings.height > 2160 or
                not math.isfinite(settings.fps) or settings.fps <= 0 or settings.fps > 60 or
                not math.isfinite(settings.segment_seconds) or settings.segment_seconds <= 0 or
                settings.segment_seconds > 60 or
                settings.segment_retention_seconds < 1 or settings.segment_retention_seconds > 3600 or
                settings.pre_event_seconds < 0 or settings.post_event_seconds < 0 or
                settings.pre_event_seconds + settings.post_event_seconds > 600 or
                settings.quota_bytes < 1 or settings.min_free_bytes < 0 or
                not math.isfinite(settings.retry_seconds) or settings.retry_seconds <= 0 or
                settings.retry_seconds > 60 or
                settings.max_pending_clips < 1 or settings.max_pending_clips > 100 or
                settings.max_clip_bytes < 1 or settings.max_clip_bytes > settings.quota_bytes or
                not settings.camera_id.strip() or settings.camera_source not in {"webcam", "rtsp"}):
            raise ValueError("configuração de captura inválida")
        if settings.camera_source == "webcam" and (not settings.camera_device or settings.camera_rtsp_url):
            raise ValueError("webcam requer CAMERA_DEVICE_PATH e não aceita CAMERA_RTSP_URL")
        if settings.camera_source == "rtsp":
            from urllib.parse import urlsplit

            try:
                uri = urlsplit(settings.camera_rtsp_url or "")
                valid_rtsp = (uri.scheme == "rtsp" and bool(uri.hostname) and uri.port is not None
                              and uri.username is None and uri.password is None and not uri.query and not uri.fragment)
            except ValueError:
                valid_rtsp = False
            if not valid_rtsp:
                raise ValueError("CAMERA_RTSP_URL deve ser RTSP local válida, sem credenciais, query ou fragmento")
        if settings.pre_event_seconds + math.ceil(settings.segment_seconds) > settings.segment_retention_seconds:
            raise ValueError("PRE_EVENT_SECONDS mais SEGMENT_SECONDS deve caber em SEGMENT_RETENTION_SECONDS")
        if settings.record_audio:
            raise ValueError("RECORD_AUDIO=true requer um adaptador de áudio da câmera, ainda indisponível")
        return settings
