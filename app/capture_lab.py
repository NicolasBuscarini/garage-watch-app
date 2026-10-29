"""Ensaio limitado da captura T00a; não homologa câmera, RTSP ou NAS."""

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

import cv2
import psutil

from app.config import Settings
from app.models import iso_utc, utc_now
from app.runtime import CaptureRuntime
from app.sources import IntermittentSyntheticSource, VideoSource, create_video_source
from app.storage import atomic_json


MAX_REPORTS = 20


def _frame_size(runtime: CaptureRuntime) -> list[int] | None:
    segments = runtime.buffer.segments
    if not segments:
        return None
    reader = cv2.VideoCapture(str(runtime.buffer.root / segments[-1].path))
    try:
        if not reader.isOpened():
            return None
        width = int(reader.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return [width, height] if width > 0 and height > 0 else None
    finally:
        reader.release()


def _persist_report(runtime: CaptureRuntime, report: dict) -> str | None:
    reports = runtime.settings.data_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    existing = sorted(reports.glob("capture-lab-*.json"), key=lambda path: path.stat().st_mtime)
    filename = f"capture-lab-{report['run_id']}.json"
    report["report_file"] = filename
    payload_bytes = len(json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    report_sizes = [(path, path.stat().st_size) for path in existing]
    non_report_bytes = runtime.archive.used_bytes() - sum(size for _, size in report_sizes)
    if non_report_bytes + payload_bytes > runtime.settings.quota_bytes:
        return None

    required_deletions = max(0, len(existing) - (MAX_REPORTS - 1))
    bytes_to_free = max(0, runtime.archive.used_bytes() + payload_bytes - runtime.settings.quota_bytes)
    deleted_bytes = 0
    paths_to_delete = []
    for path, size in report_sizes:
        if len(paths_to_delete) < required_deletions or deleted_bytes < bytes_to_free:
            paths_to_delete.append(path)
            deleted_bytes += size
    for path in paths_to_delete:
        path.unlink(missing_ok=True)
    atomic_json(reports / filename, report)
    return filename


def run_capture_lab(settings: Settings, source: VideoSource, duration_seconds: float,
                    sample_interval_seconds: float = 0.2) -> dict:
    if duration_seconds < 0.1 or duration_seconds > 86_400:
        raise ValueError("duration_seconds deve estar entre 0.1 e 86400")
    if sample_interval_seconds <= 0:
        raise ValueError("sample_interval_seconds deve ser maior que zero")

    runtime = CaptureRuntime(settings, source)
    process = psutil.Process(os.getpid())
    started_at = utc_now()
    started_monotonic = time.monotonic()
    cpu_started = process.cpu_times()
    storage_started = runtime.archive.used_bytes()
    peak_rss_bytes = process.memory_info().rss
    runtime.start()
    try:
        deadline = started_monotonic + duration_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(sample_interval_seconds, remaining))
            peak_rss_bytes = max(peak_rss_bytes, process.memory_info().rss)
    finally:
        runtime.stop()

    elapsed_seconds = time.monotonic() - started_monotonic
    cpu_finished = process.cpu_times()
    cpu_percent = ((cpu_finished.user + cpu_finished.system - cpu_started.user - cpu_started.system)
                   / elapsed_seconds * 100) if elapsed_seconds else 0
    frames_received = runtime.metrics.values.frames_received
    report = {
        "schema_version": "1.0",
        "run_id": uuid.uuid4().hex,
        "started_at": iso_utc(started_at),
        "ended_at": iso_utc(utc_now()),
        "duration_seconds": round(elapsed_seconds, 3),
        "status": "completed" if frames_received else "failed",
        "failure_reason": None if frames_received else "no_frames_received",
        "source": type(source).__name__,
        "camera_id": settings.camera_id,
        "requested_format": {"width": settings.width, "height": settings.height, "fps": settings.fps},
        "observed": {
            "frame_size": _frame_size(runtime),
            "frames_received": frames_received,
            "fps_observed": round(frames_received / elapsed_seconds, 3) if elapsed_seconds else 0,
            "gaps_total": runtime.metrics.values.gaps_total,
            "source_reconnections_total": runtime.metrics.values.source_reconnections_total,
            "segments_completed": len(runtime.buffer.segments),
            "discarded_segments_total": runtime.buffer.discarded_segments_total,
        },
        "resources": {
            "process_cpu_percent": round(cpu_percent, 3),
            "process_peak_rss_bytes": peak_rss_bytes,
            "storage_bytes_before": storage_started,
            "storage_bytes_after": runtime.archive.used_bytes(),
        },
        "limitations": [
            "O ensaio local não homologa câmera, RTSP, Wi-Fi, NAS, temperatura ou operação 24/7.",
            "A fonte sintética prova somente o comportamento local do protótipo.",
        ],
    }
    report["resources"]["storage_bytes_delta"] = (
        report["resources"]["storage_bytes_after"] - storage_started
    )
    report["report_file"] = _persist_report(runtime, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Executa ensaio local limitado de captura")
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--source", choices=("synthetic", "camera", "rtsp"), default="synthetic")
    parser.add_argument("--synthetic-fail-after-frames", type=int, default=0)
    parser.add_argument("--synthetic-failures", type=int, default=0)
    args = parser.parse_args(argv)
    if args.synthetic_fail_after_frames < 0 or args.synthetic_failures < 0:
        parser.error("falhas sintéticas devem ser zero ou positivas")

    settings = Settings.from_env()
    if args.source in {"camera", "rtsp"}:
        expected_source = "webcam" if args.source == "camera" else "rtsp"
        if settings.camera_source != expected_source:
            parser.error(f"--source {args.source} requer CAMERA_SOURCE={expected_source}")
        source: VideoSource = create_video_source(settings)
    else:
        source = IntermittentSyntheticSource(
            settings.width, settings.height, settings.fps,
            args.synthetic_fail_after_frames, args.synthetic_failures,
        )
    report = run_capture_lab(settings, source, args.duration_seconds)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
