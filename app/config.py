from dataclasses import dataclass
import math
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    camera_device: str
    camera_id: str
    width: int
    height: int
    fps: float
    segment_seconds: float
    buffer_seconds: int
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
            camera_device=required("CAMERA_DEVICE_PATH"),
            camera_id=required("CAMERA_ID"),
            width=int(required("FRAME_WIDTH")),
            height=int(required("FRAME_HEIGHT")),
            fps=float(required("FRAME_RATE")),
            segment_seconds=float(required("SEGMENT_SECONDS")),
            buffer_seconds=int(required("BUFFER_SECONDS")),
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
                settings.buffer_seconds < 1 or settings.buffer_seconds > 3600 or
                settings.pre_event_seconds < 0 or settings.post_event_seconds < 0 or
                settings.pre_event_seconds + settings.post_event_seconds > 600 or
                settings.quota_bytes < 1 or settings.min_free_bytes < 0 or
                not math.isfinite(settings.retry_seconds) or settings.retry_seconds <= 0 or
                settings.retry_seconds > 60 or
                settings.max_pending_clips < 1 or settings.max_pending_clips > 100 or
                settings.max_clip_bytes < 1 or settings.max_clip_bytes > settings.quota_bytes or
                not settings.camera_id.strip()):
            raise ValueError("configuração de captura inválida")
        if settings.pre_event_seconds > settings.buffer_seconds:
            raise ValueError("PRE_EVENT_SECONDS deve caber em BUFFER_SECONDS")
        if settings.record_audio:
            raise ValueError("RECORD_AUDIO=true requer um adaptador de áudio da câmera, ainda indisponível")
        return settings
