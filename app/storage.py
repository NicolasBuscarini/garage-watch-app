import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
from datetime import datetime

import cv2

from app.models import ClipJob, Segment, iso_utc, utc_now


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class LocalArchive:
    def __init__(self, root: Path, quota_bytes: int):
        self.root = root
        self.clips = root / "clips"
        self.jobs = root / "jobs"
        self.quota_bytes = quota_bytes
        self.clips.mkdir(parents=True, exist_ok=True)
        self.jobs.mkdir(parents=True, exist_ok=True)

    def used_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    def create_job(self, job: ClipJob) -> None:
        payload = json.dumps(job.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")
        if self.used_bytes() + len(payload) + 2048 > self.quota_bytes:
            raise OSError("cota insuficiente para persistir o job de clipe")
        atomic_json(self.jobs / f"{job.clip_id}.json", job.to_dict())

    def update_job(self, job: ClipJob) -> None:
        atomic_json(self.jobs / f"{job.clip_id}.json", job.to_dict())

    def recover(self) -> list[dict]:
        for path in self.root.rglob("*.tmp"):
            path.unlink(missing_ok=True)
        for path in self.clips.glob("*.tmp.avi"):
            path.unlink(missing_ok=True)
        recovered = []
        for media in self.clips.glob("*.avi"):
            manifest_path = media.with_suffix(".json")
            if not manifest_path.exists():
                media.unlink(missing_ok=True)
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if sha256_file(media) != manifest["sha256"]:
                    raise ValueError("checksum incompatível")
                recovered.append(manifest)
                job_path = self.jobs / f"{manifest['clip_id']}.json"
                if job_path.exists():
                    job = json.loads(job_path.read_text(encoding="utf-8"))
                    job["state"] = "archived"
                    atomic_json(job_path, job)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                had_manifest = manifest_path.exists()
                media.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                job_path = self.jobs / f"{media.stem}.json"
                if had_manifest and job_path.exists():
                    try:
                        job = json.loads(job_path.read_text(encoding="utf-8"))
                        job["state"] = "failed"
                        job["failure_reason"] = "archive_integrity_failed"
                        atomic_json(job_path, job)
                    except (OSError, json.JSONDecodeError):
                        pass
        return recovered

    def list_clips(self) -> list[dict]:
        completed = []
        for media in self.clips.glob("*.avi"):
            manifest_path = media.with_suffix(".json")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                completed.append(manifest)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return completed

    def load_jobs(self) -> list[dict]:
        jobs = []
        for path in self.jobs.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("state") in {"collecting", "ready", "failed"}:
                    jobs.append(job)
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
        return jobs

    def commit_clip(self, job: ClipJob, temporary: Path, started_at: str,
                    ended_at: str, gap_count: int, pre_buffer_complete: bool,
                    post_buffer_complete: bool) -> dict:
        if self.used_bytes() + 2048 > self.quota_bytes:
            temporary.unlink(missing_ok=True)
            raise OSError("cota de armazenamento excedida; clipe preservado como pendente")
        destination = self.clips / f"{job.clip_id}.avi"
        os.replace(temporary, destination)
        manifest = {
            **job.to_dict(),
            "state": "archived",
            "path": destination.name,
            "sha256": sha256_file(destination),
            "bytes": destination.stat().st_size,
            "started_at": started_at,
            "ended_at": ended_at,
            "gap_count": gap_count,
            "pre_buffer_complete": pre_buffer_complete,
            "post_buffer_complete": post_buffer_complete,
        }
        atomic_json(destination.with_suffix(".json"), manifest)
        job.state = "archived"
        job.gap_count = gap_count
        self.update_job(job)
        return manifest


class SegmentBuffer:
    def __init__(self, root: Path, camera_id: str, stream_epoch: str,
                 fps: float, segment_seconds: float, buffer_seconds: int,
                 archive: LocalArchive, min_free_bytes: int, on_discard=None, on_gap=None):
        self.root = root / "segments"
        self.root.mkdir(parents=True, exist_ok=True)
        self.camera_id, self.stream_epoch = camera_id, stream_epoch
        self.fps, self.segment_seconds, self.buffer_seconds = fps, segment_seconds, buffer_seconds
        self.archive, self.min_free_bytes = archive, min_free_bytes
        self.on_discard = on_discard or (lambda: None)
        self.on_gap = on_gap or (lambda: None)
        self.writer = None
        self.current_path: Path | None = None
        self.current_started = None
        self.current_ended = None
        self.sequence = 0
        self.segments: list[Segment] = []
        self.gaps: list[dict] = []
        self.discarded_segments_total = 0
        self._quota_gap_start = None
        self._recover()

    def _recover(self) -> None:
        for path in self.root.glob("*.tmp"):
            path.unlink(missing_ok=True)
        for sidecar in sorted(self.root.glob("[0-9]*-*.json")):
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                media = self.root / data["path"]
                if not media.exists() or sha256_file(media) != data["sha256"]:
                    raise ValueError("segmento incompleto")
                self.segments.append(Segment(
                    data["segment_id"], data["camera_id"], data["stream_epoch"],
                    datetime.fromisoformat(data["started_at"].replace("Z", "+00:00")),
                    datetime.fromisoformat(data["ended_at"].replace("Z", "+00:00")),
                    data["path"], data["sha256"], data["bytes"], data["sequence"],
                ))
                self.sequence = max(self.sequence, data["sequence"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                sidecar.unlink(missing_ok=True)
        valid = {segment.path for segment in self.segments}
        for media in self.root.glob("*.avi"):
            if media.name not in valid:
                media.unlink(missing_ok=True)
                gap_at = utc_now()
                gap = {"camera_id": self.camera_id, "stream_epoch": self.stream_epoch,
                       "started_at": iso_utc(gap_at), "ended_at": iso_utc(gap_at),
                       "reason": "orphan_segment_removed"}
                self.gaps.append(gap)
                self.on_gap()
                atomic_json(self.root / f"gap-{uuid.uuid4().hex}.json", gap)

    def _pinned(self, segment: Segment) -> bool:
        for data in self.archive.load_jobs():
            if data.get("state") not in {"collecting", "ready"}:
                continue
            from datetime import datetime
            start = datetime.fromisoformat(data["window_start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(data["window_end"].replace("Z", "+00:00"))
            if segment.ended_at >= start and segment.started_at <= end:
                return True
        return False

    def _prune(self, now) -> None:
        keep_after = now.timestamp() - self.buffer_seconds
        retained = []
        for segment in self.segments:
            if segment.ended_at.timestamp() < keep_after and not self._pinned(segment):
                (self.root / segment.path).unlink(missing_ok=True)
                (self.root / f"{segment.segment_id}.json").unlink(missing_ok=True)
                self.discarded_segments_total += 1
                self.on_discard()
            else:
                retained.append(segment)
        self.segments = retained

    def write(self, frame, captured_at) -> None:
        if self.writer is None:
            self._prune(captured_at)
            usage = shutil.disk_usage(self.root)
            if self.archive.used_bytes() + 2048 >= self.archive.quota_bytes or usage.free <= self.min_free_bytes:
                if self._quota_gap_start is None:
                    self._quota_gap_start = captured_at
                    self.on_gap()
                return
            if self._quota_gap_start is not None:
                gap = {"camera_id": self.camera_id, "stream_epoch": self.stream_epoch,
                       "started_at": iso_utc(self._quota_gap_start), "ended_at": iso_utc(captured_at),
                       "reason": "storage_quota"}
                self.gaps.append(gap)
                atomic_json(self.root / f"gap-{uuid.uuid4().hex}.json", gap)
                self._quota_gap_start = None
            self.sequence += 1
            segment_id = f"{self.sequence:08d}-{uuid.uuid4().hex[:8]}"
            self.current_path = self.root / f"{segment_id}.tmp.avi"
            frame_size = (int(frame.shape[1]), int(frame.shape[0]))
            self.writer = cv2.VideoWriter(str(self.current_path), cv2.VideoWriter_fourcc(*"MJPG"),
                                          self.fps, frame_size)
            if not self.writer.isOpened():
                self.writer.release()
                self.writer = None
                self.current_path.unlink(missing_ok=True)
                raise OSError("não foi possível iniciar o gravador de segmentos")
            self.current_started = captured_at
        self.writer.write(frame)
        self.current_ended = captured_at
        if (captured_at - self.current_started).total_seconds() >= self.segment_seconds:
            self.close_segment()

    def close_segment(self) -> Segment | None:
        if self.writer is None or self.current_path is None:
            self.flush_quota_gap()
            return None
        self.writer.release()
        temporary = self.current_path
        segment_id = temporary.name.removesuffix(".tmp.avi")
        final = self.root / f"{segment_id}.avi"
        media_size = temporary.stat().st_size if temporary.exists() else 0
        if not temporary.exists() or media_size == 0 or self.archive.used_bytes() + 2048 > self.archive.quota_bytes:
            temporary.unlink(missing_ok=True)
            gap = {"camera_id": self.camera_id, "stream_epoch": self.stream_epoch,
                   "started_at": iso_utc(self.current_started), "ended_at": iso_utc(self.current_ended),
                   "reason": "storage_quota"}
            self.gaps.append(gap)
            self.on_gap()
            atomic_json(self.root / f"gap-{uuid.uuid4().hex}.json", gap)
            segment = None
        else:
            os.replace(temporary, final)
            segment = Segment(segment_id, self.camera_id, self.stream_epoch,
                              self.current_started, self.current_ended, final.name,
                              sha256_file(final), final.stat().st_size, self.sequence)
            atomic_json(self.root / f"{segment_id}.json", segment.to_dict())
            self.segments.append(segment)
            self._prune(self.current_ended)
        self.writer = None
        self.current_path = None
        self.current_started = self.current_ended = None
        self.flush_quota_gap()
        return segment

    def flush_quota_gap(self) -> None:
        if self._quota_gap_start is None:
            return
        gap = {"camera_id": self.camera_id, "stream_epoch": self.stream_epoch,
               "started_at": iso_utc(self._quota_gap_start), "ended_at": iso_utc(utc_now()),
               "reason": "storage_quota"}
        self.gaps.append(gap)
        atomic_json(self.root / f"gap-{uuid.uuid4().hex}.json", gap)
        self._quota_gap_start = None
