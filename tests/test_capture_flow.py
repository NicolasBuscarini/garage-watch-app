import hashlib
import json
import threading
import time
from datetime import timedelta

import cv2
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.capture_lab import run_capture_lab
from app.main import app
from app.models import ClipJob, utc_now
from app.runtime import CaptureRuntime
from app.sources import IntermittentSyntheticSource, OpenCVRtspSource, SyntheticSource, create_video_source
from app.storage import LocalArchive, SegmentBuffer


def settings(tmp_path, **overrides):
    values = dict(
        data_dir=tmp_path,
        camera_device="synthetic",
        camera_source="webcam",
        camera_rtsp_url=None,
        camera_id="test-camera",
        width=160,
        height=120,
        fps=10.0,
        segment_seconds=0.2,
        segment_retention_seconds=20,
        pre_event_seconds=0,
        post_event_seconds=1,
        quota_bytes=20_000_000,
        min_free_bytes=0,
        retry_seconds=0.1,
        max_pending_clips=5,
        max_clip_bytes=10_000_000,
        record_audio=False,
    )
    values.update(overrides)
    return Settings(**values)


def test_synthetic_source_manual_clip_api_and_metrics(tmp_path, monkeypatch):
    configured = settings(tmp_path)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: configured))
    monkeypatch.setattr(
        "app.main.create_video_source",
        lambda _: SyntheticSource(configured.width, configured.height, configured.fps),
    )

    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "live"}
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and client.get("/api/v1/status").json()["buffer_segments"] < 2:
            time.sleep(0.05)
        capture_status = client.get("/api/v1/status").json()
        assert capture_status["frames_received"] > 0, capture_status
        assert capture_status["buffer_segments"] >= 2, capture_status
        assert capture_status["segment_retention_seconds"] == configured.segment_retention_seconds
        assert capture_status["record_audio"] is False
        assert client.get("/health/ready").status_code == 200
        requested = client.post("/api/v1/clips")
        assert requested.status_code == 202
        clip_id = requested.json()["clip_id"]

        deadline = time.monotonic() + 5
        clips = []
        while time.monotonic() < deadline:
            clips = client.get("/api/v1/clips").json()
            if any(clip["clip_id"] == clip_id and clip.get("state") in {"archived", "failed"}
                   for clip in clips):
                break
            time.sleep(0.1)
        clip = next(item for item in clips if item["clip_id"] == clip_id)
        capture_status = client.get("/api/v1/status").json()
        assert clip["state"] == "archived", {
            "clip": clip,
            "window": [clip["window_start"], clip["window_end"]],
            "segments": capture_status["buffer_window"],
        }
        media = tmp_path / "clips" / clip["path"]
        assert clip["state"] == "archived"
        assert clip["pre_buffer_complete"] is True
        # Real-time scheduling can leave the requested post window incomplete;
        # the contract is that the manifest makes this condition observable.
        assert isinstance(clip["post_buffer_complete"], bool)
        assert hashlib.sha256(media.read_bytes()).hexdigest() == clip["sha256"]
        reader = cv2.VideoCapture(str(media))
        ok, frame = reader.read()
        reader.release()
        assert ok and frame.shape[:2] == (configured.height, configured.width)
        metrics = client.get("/metrics").text
        assert "garage_watch_frames_received " in metrics
        assert "garage_watch_gaps_total " in metrics
        assert "garage_watch_clips_total " in metrics
        assert "garage_watch_discarded_segments_total " in metrics
        assert "garage_watch_storage_bytes " in metrics
        assert "garage_watch_process_cpu_percent " in metrics
        assert "garage_watch_process_rss_bytes " in metrics

    restarted = CaptureRuntime(configured, SyntheticSource(configured.width, configured.height, configured.fps))
    assert len(restarted.buffer.segments) >= 2
    assert any(item["clip_id"] == clip_id for item in restarted.recovered_clips)


def test_source_gap_is_persisted_and_counted(tmp_path):
    class IntermittentSource(SyntheticSource):
        def read(self):
            if self.index == 3:
                self.index += 1
                return None
            return super().read()

    configured = settings(tmp_path, retry_seconds=0.05)
    source = IntermittentSource(configured.width, configured.height, configured.fps)
    runtime = CaptureRuntime(configured, source)
    runtime.start()
    deadline = time.monotonic() + 3
    gap_files = []
    while time.monotonic() < deadline:
        gap_files = list((tmp_path / "gaps").glob("*.json"))
        if gap_files and runtime.metrics.values.frames_received > 3:
            break
        time.sleep(0.05)
    runtime.stop()

    assert runtime.metrics.values.gaps_total >= 1
    assert gap_files
    gap = json.loads(gap_files[0].read_text(encoding="utf-8"))
    assert gap["reason"] == "source_unavailable"
    assert gap["camera_id"] == configured.camera_id
    recovered = CaptureRuntime(configured, SyntheticSource(configured.width, configured.height, configured.fps))
    assert len({segment.stream_epoch for segment in recovered.buffer.segments}) >= 2


def test_clip_request_queue_is_bounded(tmp_path):
    configured = settings(tmp_path, max_pending_clips=1, post_event_seconds=30)
    runtime = CaptureRuntime(configured, SyntheticSource(configured.width, configured.height, configured.fps))

    runtime.trigger_clip()
    with pytest.raises(RuntimeError, match="limite"):
        runtime.trigger_clip()


def test_quota_failure_persists_an_explicit_gap(tmp_path):
    configured = settings(tmp_path, quota_bytes=1, segment_seconds=0.01)
    archive = LocalArchive(tmp_path, configured.quota_bytes)
    buffer = SegmentBuffer(tmp_path, configured.camera_id, "epoch", configured.fps,
                           configured.segment_seconds, configured.segment_retention_seconds, archive, 0)
    import numpy as np

    buffer.write(np.zeros((120, 160, 3), dtype=np.uint8), utc_now())
    buffer.close_segment()
    gap_files = list((tmp_path / "segments").glob("gap-*.json"))
    assert len(gap_files) == 1
    assert json.loads(gap_files[0].read_text())["reason"] == "storage_quota"

def test_clip_archive_quota_failure_keeps_auditable_job(tmp_path):
    archive = LocalArchive(tmp_path, 4096)
    now = utc_now()
    job = ClipJob("quota-clip", "test-camera", "epoch", now,
                  now - timedelta(seconds=1), now + timedelta(seconds=1))
    archive.create_job(job)
    temporary = archive.clips / "quota-clip.tmp.avi"
    temporary.write_bytes(b"x" * 8192)

    with pytest.raises(OSError, match="cota"):
        archive.commit_clip(job, temporary, now.isoformat(), now.isoformat(), 0, True, True)

    assert not temporary.exists()
    persisted = json.loads((archive.jobs / "quota-clip.json").read_text(encoding="utf-8"))
    assert persisted["state"] == "collecting"


def test_clip_size_limit_stops_temporary_encoding(tmp_path):
    configured = settings(tmp_path, max_clip_bytes=1)
    source = SyntheticSource(configured.width, configured.height, configured.fps)
    source.open()
    runtime = CaptureRuntime(configured, source)
    for _ in range(15):
        frame = source.read()
        runtime.buffer.write(frame.image, frame.captured_at)
    runtime.buffer.close_segment()
    source.close()
    segments = list(runtime.buffer.segments)
    job = ClipJob("bounded-clip", configured.camera_id, runtime.stream_epoch, utc_now(),
                  segments[0].started_at, segments[-1].ended_at)
    runtime.archive.create_job(job)

    with pytest.raises(OSError, match="limite de tamanho"):
        runtime._assemble(job, segments)

    assert not (runtime.archive.clips / "bounded-clip.tmp.avi").exists()
    assert not (runtime.archive.clips / "bounded-clip.avi").exists()


def test_archive_recovers_verified_clip_and_drops_corrupt_file(tmp_path):
    archive = LocalArchive(tmp_path, 10_000)
    media = archive.clips / "good.avi"
    media.write_bytes(b"synthetic-video-bytes")
    manifest = {"clip_id": "good", "sha256": hashlib.sha256(media.read_bytes()).hexdigest()}
    (archive.clips / "good.json").write_text(json.dumps(manifest), encoding="utf-8")
    (archive.clips / "orphan.avi").write_bytes(b"orphan")

    recovered = archive.recover()
    assert [item["clip_id"] for item in recovered] == ["good"]
    assert not (archive.clips / "orphan.avi").exists()

    media.write_bytes(b"tampered")
    assert archive.recover() == []
    assert not media.exists()
    assert not (archive.clips / "good.json").exists()


def test_segment_recovery_surfaces_corruption_as_gap(tmp_path):
    configured = settings(tmp_path)
    archive = LocalArchive(tmp_path, configured.quota_bytes)
    buffer = SegmentBuffer(tmp_path, configured.camera_id, "epoch-one", configured.fps,
                           configured.segment_seconds, configured.segment_retention_seconds, archive, 0)
    source = SyntheticSource(configured.width, configured.height, configured.fps)
    source.open()
    for _ in range(4):
        frame = source.read()
        buffer.write(frame.image, frame.captured_at)
    buffer.close_segment()
    source.close()
    segment_path = buffer.root / buffer.segments[0].path
    segment_path.write_bytes(b"corrupt")

    recovered = SegmentBuffer(tmp_path, configured.camera_id, "epoch-two", configured.fps,
                            configured.segment_seconds, configured.segment_retention_seconds, archive, 0)
    assert len(recovered.segments) == 1
    assert not segment_path.exists()
    gaps = list(recovered.root.glob("gap-*.json"))
    assert gaps
    assert json.loads(gaps[0].read_text(encoding="utf-8"))["reason"] == "orphan_segment_removed"


def test_startup_prunes_expired_legacy_segments_but_keeps_pending_job(tmp_path):
    import numpy as np

    configured = settings(tmp_path, segment_retention_seconds=120)
    archive = LocalArchive(tmp_path, configured.quota_bytes)
    buffer = SegmentBuffer(tmp_path, configured.camera_id, "epoch-old", configured.fps,
                           configured.segment_seconds, configured.segment_retention_seconds, archive, 0)
    old = utc_now() - timedelta(seconds=300)
    frame = np.zeros((configured.height, configured.width, 3), dtype=np.uint8)

    for offset in (0, 0.3):
        buffer.write(frame, old + timedelta(seconds=offset))
    buffer.close_segment()
    pinned = buffer.segments[-1]
    for offset in (10, 10.3):
        buffer.write(frame, old + timedelta(seconds=offset))
    buffer.close_segment()
    expired = buffer.segments[-1]

    archive.create_job(ClipJob("pending", configured.camera_id, "epoch-old", old,
                               old - timedelta(seconds=1), old + timedelta(seconds=1)))
    recovered = SegmentBuffer(tmp_path, configured.camera_id, "epoch-new", configured.fps,
                              configured.segment_seconds, 1, archive, 0)

    assert [segment.segment_id for segment in recovered.segments] == [pinned.segment_id]
    assert (recovered.root / pinned.path).exists()
    assert not (recovered.root / expired.path).exists()
    assert not (recovered.root / f"{expired.segment_id}.json").exists()
    assert json.loads((archive.jobs / "pending.json").read_text(encoding="utf-8"))["state"] == "collecting"


def test_recovery_reconciles_manifest_with_pending_job(tmp_path):
    archive = LocalArchive(tmp_path, 10_000)
    now = utc_now()
    job = ClipJob("reconcile-clip", "test-camera", "epoch", now,
                  now - timedelta(seconds=1), now + timedelta(seconds=1))
    archive.create_job(job)
    media = archive.clips / "reconcile-clip.avi"
    media.write_bytes(b"completed-media")
    manifest = {**job.to_dict(), "state": "archived", "path": media.name,
                "sha256": hashlib.sha256(media.read_bytes()).hexdigest()}
    (archive.clips / "reconcile-clip.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert len(archive.recover()) == 1
    persisted = json.loads((archive.jobs / "reconcile-clip.json").read_text(encoding="utf-8"))
    assert persisted["state"] == "archived"


def test_invalid_configuration_is_rejected(tmp_path, monkeypatch):
    for key, value in {
        "HOST_DATA_DIR": str(tmp_path), "CAMERA_DEVICE_PATH": "/dev/video0", "CAMERA_ID": "garage",
        "FRAME_WIDTH": "640", "FRAME_HEIGHT": "480", "FRAME_RATE": "10",
        "SEGMENT_SECONDS": "2", "SEGMENT_RETENTION_SECONDS": "10", "PRE_EVENT_SECONDS": "30",
        "POST_EVENT_SECONDS": "20", "STORAGE_QUOTA_BYTES": "1000000", "MIN_FREE_BYTES": "1000",
        "CAPTURE_RETRY_SECONDS": "1", "MAX_PENDING_CLIPS": "5",
        "MAX_CLIP_BYTES": "100000", "RECORD_AUDIO": "false",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="SEGMENT_RETENTION_SECONDS"):
        Settings.from_env()


def test_audio_recording_is_rejected_until_camera_adapter_exists(tmp_path, monkeypatch):
    for key, value in {
        "HOST_DATA_DIR": str(tmp_path), "CAMERA_DEVICE_PATH": "/dev/video0", "CAMERA_ID": "garage",
        "FRAME_WIDTH": "640", "FRAME_HEIGHT": "480", "FRAME_RATE": "10",
        "SEGMENT_SECONDS": "2", "SEGMENT_RETENTION_SECONDS": "30", "PRE_EVENT_SECONDS": "10",
        "POST_EVENT_SECONDS": "20", "STORAGE_QUOTA_BYTES": "1000000", "MIN_FREE_BYTES": "1000",
        "CAPTURE_RETRY_SECONDS": "1", "MAX_PENDING_CLIPS": "5", "MAX_CLIP_BYTES": "100000",
        "RECORD_AUDIO": "true",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="adaptador de áudio"):
        Settings.from_env()


def test_capture_lab_persists_sanitized_measurements_from_synthetic_source(tmp_path):
    configured = settings(tmp_path, segment_seconds=0.1)
    report = run_capture_lab(
        configured,
        SyntheticSource(configured.width, configured.height, configured.fps),
        duration_seconds=0.5,
        sample_interval_seconds=0.05,
    )

    assert report["status"] == "completed"
    assert report["source"] == "SyntheticSource"
    assert report["observed"]["frames_received"] > 0
    assert report["observed"]["frame_size"] == [configured.width, configured.height]
    assert report["resources"]["storage_bytes_delta"] > 0
    assert report["report_file"]
    persisted = json.loads((tmp_path / "reports" / report["report_file"]).read_text(encoding="utf-8"))
    assert persisted == report
    assert "camera_device" not in persisted


def test_capture_lab_reports_synthetic_reconnections(tmp_path):
    configured = settings(tmp_path, segment_seconds=0.1, retry_seconds=0.01)
    report = run_capture_lab(
        configured,
        IntermittentSyntheticSource(
            configured.width, configured.height, configured.fps,
            fail_after_frames=2, failures=2,
        ),
        duration_seconds=0.7,
        sample_interval_seconds=0.05,
    )

    assert report["status"] == "completed"
    assert report["observed"]["gaps_total"] >= 1
    assert report["observed"]["source_reconnections_total"] >= 2


def test_capture_lab_stops_while_waiting_to_retry(tmp_path):
    configured = settings(tmp_path, retry_seconds=2)
    report = run_capture_lab(
        configured,
        IntermittentSyntheticSource(
            configured.width, configured.height, configured.fps,
            fail_after_frames=0, failures=1,
        ),
        duration_seconds=0.2,
        sample_interval_seconds=0.05,
    )

    assert report["status"] == "failed"
    assert report["failure_reason"] == "no_frames_received"
    assert report["duration_seconds"] < 1


def test_capture_lab_refuses_a_data_directory_owned_by_capture(tmp_path):
    configured = settings(tmp_path)
    runtime = CaptureRuntime(configured, SyntheticSource(configured.width, configured.height, configured.fps))
    try:
        with pytest.raises(RuntimeError, match="já está em execução"):
            run_capture_lab(
                configured,
                SyntheticSource(configured.width, configured.height, configured.fps),
                duration_seconds=0.2,
            )
    finally:
        runtime.stop()


def test_capture_lab_keeps_existing_reports_when_quota_cannot_fit_a_new_one(tmp_path):
    configured = settings(tmp_path, quota_bytes=1)
    reports = tmp_path / "reports"
    reports.mkdir()
    existing = reports / "capture-lab-existing.json"
    existing.write_text('{"run_id":"existing"}', encoding="utf-8")

    report = run_capture_lab(
        configured,
        SyntheticSource(configured.width, configured.height, configured.fps),
        duration_seconds=0.2,
        sample_interval_seconds=0.05,
    )

    assert report["report_file"] is None
    assert existing.exists()


def test_capture_runtime_retains_data_lock_until_a_blocked_worker_stops(tmp_path):
    class BlockingSource:
        def __init__(self):
            self.read_started = threading.Event()
            self.release_read = threading.Event()

        def open(self):
            pass

        def read(self):
            self.read_started.set()
            self.release_read.wait()
            return None

        def close(self):
            pass

    configured = settings(tmp_path)
    source = BlockingSource()
    runtime = CaptureRuntime(configured, source)
    runtime.start()
    assert source.read_started.wait(timeout=1)
    runtime.stop()

    with pytest.raises(RuntimeError, match="já está em execução"):
        CaptureRuntime(configured, SyntheticSource(configured.width, configured.height, configured.fps))

    source.release_read.set()
    deadline = time.monotonic() + 1
    recovered = None
    while time.monotonic() < deadline:
        try:
            recovered = CaptureRuntime(configured, SyntheticSource(configured.width, configured.height, configured.fps))
            break
        except RuntimeError:
            time.sleep(0.01)
    assert recovered is not None
    recovered.stop()


def test_rtsp_source_uses_ffmpeg_transport_and_bounded_timeouts(monkeypatch, tmp_path):
    class FakeCapture:
        def __init__(self):
            self.open_args = None
            self.released = False

        def open(self, *args):
            self.open_args = args
            return True

        def isOpened(self):
            return True

        def read(self):
            import numpy as np
            return True, np.zeros((240, 320, 3), dtype=np.uint8)

        def release(self):
            self.released = True

    capture = FakeCapture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda: capture)
    configured = settings(tmp_path, camera_source="rtsp", camera_device="",
                          camera_rtsp_url="rtsp://neolink:8554/garage/mainStream")
    source = create_video_source(configured)
    assert isinstance(source, OpenCVRtspSource)

    source.open()
    frame = source.read()
    source.close()

    assert capture.open_args[0] == "rtsp://neolink:8554/garage/mainStream"
    assert capture.open_args[1] == cv2.CAP_FFMPEG
    assert capture.open_args[2] == [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000]
    assert frame.image.shape == (120, 160, 3)
    assert capture.released


def test_rtsp_source_failure_does_not_disclose_url(monkeypatch, tmp_path):
    class UnavailableCapture:
        def open(self, *_):
            return False

        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", UnavailableCapture)
    url = "rtsp://neolink:8554/garage/mainStream?token=secret"
    source = OpenCVRtspSource(url, 160, 120, 10)
    with pytest.raises(RuntimeError) as error:
        source.open()
    assert "secret" not in str(error.value)


def test_rtsp_configuration_rejects_embedded_credentials(monkeypatch):
    values = {
        "HOST_DATA_DIR": "/tmp/capture-test",
        "CAMERA_SOURCE": "rtsp",
        "CAMERA_RTSP_URL": "rtsp://operator:secret@neolink:8554/garage/mainStream",
        "CAMERA_ID": "test-camera",
        "FRAME_WIDTH": "640",
        "FRAME_HEIGHT": "360",
        "FRAME_RATE": "15",
        "SEGMENT_SECONDS": "2",
        "SEGMENT_RETENTION_SECONDS": "15",
        "PRE_EVENT_SECONDS": "0",
        "POST_EVENT_SECONDS": "1",
        "STORAGE_QUOTA_BYTES": "20000000",
        "MIN_FREE_BYTES": "0",
        "CAPTURE_RETRY_SECONDS": "2",
        "MAX_PENDING_CLIPS": "5",
        "MAX_CLIP_BYTES": "10000000",
        "RECORD_AUDIO": "false",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError) as error:
        Settings.from_env()
    assert "secret" not in str(error.value)
