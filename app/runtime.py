from datetime import datetime, timedelta, timezone
import shutil
import threading
import time
import uuid

import cv2

from app.config import Settings
from app.metrics import Metrics
from app.models import ClipJob, iso_utc, utc_now
from app.sources import VideoSource
from app.storage import LocalArchive, SegmentBuffer, atomic_json


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class CaptureRuntime:
    def __init__(self, settings: Settings, source: VideoSource):
        self.settings = settings
        self.source = source
        self.archive = LocalArchive(settings.data_dir, settings.quota_bytes)
        self.recovered_clips = self.archive.recover()
        self.stream_epoch = uuid.uuid4().hex
        self.metrics = Metrics(settings.data_dir)
        self.buffer = SegmentBuffer(settings.data_dir, settings.camera_id, self.stream_epoch,
                                    settings.fps, settings.segment_seconds, settings.buffer_seconds,
                                    self.archive, settings.min_free_bytes,
                                    on_discard=lambda: self.metrics.increment("discarded_segments_total"),
                                    on_gap=lambda: self.metrics.increment("gaps_total"))
        self.restart_gap_pending = bool(self.buffer.segments)
        self.stop_event = threading.Event()
        self.capture_thread: threading.Thread | None = None
        self.clip_thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.gap_start: datetime | None = None
        self.started = False
        self.source_connected = False

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.capture_thread = threading.Thread(target=self._capture_loop, name="capture", daemon=True)
        self.clip_thread = threading.Thread(target=self._clip_loop, name="clips", daemon=True)
        self.capture_thread.start()
        self.clip_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.source.close()
        if self.capture_thread:
            self.capture_thread.join(timeout=5)
        if self.clip_thread:
            self.clip_thread.join(timeout=5)
        with self.lock:
            self.buffer.close_segment()
        if self.gap_start is not None:
            self._persist_gap(self.gap_start, utc_now(), "source_unavailable")
            self.gap_start = None
        self.started = False

    def _capture_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.source.open()
                self.source_connected = True
                while not self.stop_event.is_set():
                    frame = self.source.read()
                    if frame is None:
                        if self.gap_start is None:
                            self.gap_start = utc_now()
                            self.metrics.increment("gaps_total")
                        with self.lock:
                            self.buffer.close_segment()
                        self.source.close()
                        self.source_connected = False
                        time.sleep(self.settings.retry_seconds)
                        break
                    if self.gap_start is not None:
                        self._persist_gap(self.gap_start, frame.captured_at, "source_unavailable")
                        self.stream_epoch = uuid.uuid4().hex
                        self.buffer.stream_epoch = self.stream_epoch
                        self.gap_start = None
                        self.restart_gap_pending = False
                    elif self.restart_gap_pending and self.buffer.segments:
                        self.restart_gap_pending = False
                        previous = max(self.buffer.segments, key=lambda segment: segment.ended_at)
                        if (frame.captured_at - previous.ended_at).total_seconds() > self.settings.segment_seconds * 1.5:
                            self.metrics.increment("gaps_total")
                            self._persist_gap(previous.ended_at, frame.captured_at, "capture_restart_gap",
                                              previous.stream_epoch)
                    with self.lock:
                        self.buffer.write(frame.image, frame.captured_at)
                    self.metrics.frame((utc_now() - frame.captured_at).total_seconds())
            except Exception:
                if self.gap_start is None:
                    self.gap_start = utc_now()
                    self.metrics.increment("gaps_total")
                self.source.close()
                self.source_connected = False
                self.stop_event.wait(self.settings.retry_seconds)

    def _persist_gap(self, started: datetime, ended: datetime, reason: str,
                     stream_epoch: str | None = None) -> None:
        gap = {"camera_id": self.settings.camera_id, "stream_epoch": stream_epoch or self.stream_epoch,
               "started_at": iso_utc(started), "ended_at": iso_utc(ended), "reason": reason}
        path = self.settings.data_dir / "gaps" / f"{uuid.uuid4().hex}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, gap)

    def trigger_clip(self) -> ClipJob:
        with self.lock:
            pending = [job for job in self.archive.load_jobs()
                       if job.get("state") in {"collecting", "ready"}]
            if len(pending) >= self.settings.max_pending_clips:
                raise RuntimeError("limite de clipes pendentes atingido")
            now = utc_now()
            job = ClipJob(uuid.uuid4().hex, self.settings.camera_id, self.stream_epoch, now,
                          now - timedelta(seconds=self.settings.pre_event_seconds),
                          now + timedelta(seconds=self.settings.post_event_seconds))
            self.archive.create_job(job)
            return job

    def _clip_loop(self) -> None:
        while not self.stop_event.wait(0.2):
            for data in self.archive.load_jobs():
                try:
                    job = ClipJob(data["clip_id"], data["camera_id"], data["stream_epoch"],
                                  parse_time(data["requested_at"]), parse_time(data["window_start"]),
                                  parse_time(data["window_end"]), data["state"], data.get("gap_count", 0))
                    if job.state in {"ready", "failed"}:
                        continue
                    if utc_now() < job.window_end:
                        continue
                    with self.lock:
                        self.buffer.close_segment()
                        segments = [segment for segment in self.buffer.segments
                                    if segment.ended_at >= job.window_start and segment.started_at <= job.window_end]
                    self._assemble(job, segments)
                except Exception as exc:
                    data["state"] = "failed"
                    data["failure_reason"] = type(exc).__name__
                    atomic_json(self.archive.jobs / f"{data['clip_id']}.json", data)

    def _assemble(self, job: ClipJob, segments) -> None:
        if not segments:
            job.state = "failed"
            job.gap_count = 1
            data = job.to_dict()
            data["failure_reason"] = "no_buffered_segments"
            self.archive.update_job(job)
            atomic_json(self.archive.jobs / f"{job.clip_id}.json", data)
            self.metrics.increment("gaps_total")
            return
        if sum(segment.bytes for segment in segments) > self.settings.max_clip_bytes:
            raise OSError("limite de tamanho do clipe excedido")
        free_bytes = shutil.disk_usage(self.archive.root).free
        if free_bytes < self.settings.min_free_bytes + self.settings.max_clip_bytes:
            raise OSError("reserva de espaço insuficiente para montar o clipe")
        temp = self.archive.clips / f"{job.clip_id}.tmp.avi"
        capture = None
        writer = None
        previous_end = None
        gaps = 0
        first_frame_at = None
        last_frame_at = None
        frame_count = 0
        try:
            for segment in segments:
                if previous_end and (segment.started_at - previous_end).total_seconds() > self.settings.segment_seconds * 1.5:
                    gaps += 1
                capture = cv2.VideoCapture(str(self.buffer.root / segment.path))
                if not capture.isOpened():
                    gaps += 1
                    capture.release()
                    capture = None
                    continue
                if writer is None:
                    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    writer = cv2.VideoWriter(str(temp), cv2.VideoWriter_fourcc(*"MJPG"), self.settings.fps,
                                             (width, height))
                    if not writer.isOpened():
                        raise OSError("não foi possível iniciar o gravador de clipes")
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    writer.write(frame)
                    frame_count += 1
                    if frame_count % 10 == 0 and temp.exists() and temp.stat().st_size > self.settings.max_clip_bytes:
                        raise OSError("limite de tamanho do clipe excedido")
                    first_frame_at = first_frame_at or segment.started_at
                    last_frame_at = segment.ended_at
                capture.release()
                capture = None
                previous_end = segment.ended_at
            if writer is None:
                raise OSError("nenhum segmento reproduzível no intervalo do clipe")
            writer.release()
            writer = None
            if temp.stat().st_size > self.settings.max_clip_bytes:
                raise OSError("limite de tamanho do clipe excedido")
            self.archive.commit_clip(
                job, temp, iso_utc(first_frame_at), iso_utc(last_frame_at), gaps,
                self.settings.pre_event_seconds == 0 or first_frame_at <= job.window_start + timedelta(seconds=1 / self.settings.fps),
                last_frame_at + timedelta(seconds=1 / self.settings.fps) >= job.window_end,
            )
            self.metrics.increment("clips_total")
        finally:
            if capture:
                capture.release()
            if writer:
                writer.release()
            temp.unlink(missing_ok=True)

    def clips(self) -> list[dict]:
        completed = self.archive.list_clips()
        completed_ids = {item["clip_id"] for item in completed}
        pending = [item for item in self.archive.load_jobs() if item["clip_id"] not in completed_ids]
        return completed + pending

    def status(self) -> dict:
        return {
            "camera_id": self.settings.camera_id,
            "stream_epoch": self.stream_epoch,
            "source": type(self.source).__name__,
            "frames_received": self.metrics.values.frames_received,
            "gaps": self.metrics.values.gaps_total,
            "storage_bytes": self.archive.used_bytes(),
            "storage_quota_bytes": self.settings.quota_bytes,
            "buffer_segments": len(self.buffer.segments),
            "discarded_segments": self.buffer.discarded_segments_total,
            "source_connected": self.source_connected,
            "buffer_window": [
                {"started_at": iso_utc(segment.started_at), "ended_at": iso_utc(segment.ended_at)}
                for segment in self.buffer.segments[-3:]
            ],
        }
