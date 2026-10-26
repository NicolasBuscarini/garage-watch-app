from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Frame:
    image: object
    captured_at: datetime


@dataclass
class Segment:
    segment_id: str
    camera_id: str
    stream_epoch: str
    started_at: datetime
    ended_at: datetime
    path: str
    sha256: str
    bytes: int
    sequence: int

    def to_dict(self) -> dict:
        value = asdict(self)
        value["started_at"] = iso_utc(self.started_at)
        value["ended_at"] = iso_utc(self.ended_at)
        return value


@dataclass
class ClipJob:
    clip_id: str
    camera_id: str
    stream_epoch: str
    requested_at: datetime
    window_start: datetime
    window_end: datetime
    state: str = "collecting"
    gap_count: int = 0

    def to_dict(self) -> dict:
        return {
            "clip_id": self.clip_id,
            "camera_id": self.camera_id,
            "stream_epoch": self.stream_epoch,
            "requested_at": iso_utc(self.requested_at),
            "window_start": iso_utc(self.window_start),
            "window_end": iso_utc(self.window_end),
            "state": self.state,
            "gap_count": self.gap_count,
        }
