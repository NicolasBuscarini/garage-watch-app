from dataclasses import dataclass
import os
import threading

import psutil


@dataclass
class CaptureMetrics:
    frames_received: int = 0
    gaps_total: int = 0
    clips_total: int = 0
    discarded_segments_total: int = 0
    last_frame_age_seconds: float = 0


class Metrics:
    def __init__(self, storage_root):
        self.values = CaptureMetrics()
        self.lock = threading.Lock()
        self.storage_root = storage_root
        self.process = psutil.Process(os.getpid())

    def increment(self, name: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self.values, name, getattr(self.values, name) + amount)

    def frame(self, age_seconds: float) -> None:
        with self.lock:
            self.values.frames_received += 1
            self.values.last_frame_age_seconds = max(0, age_seconds)

    def render(self) -> str:
        with self.lock:
            values = vars(self.values).copy()
        disk_bytes = 0
        if self.storage_root.exists():
            for path in self.storage_root.rglob("*"):
                try:
                    if path.is_file():
                        disk_bytes += path.stat().st_size
                except FileNotFoundError:
                    continue
        try:
            cpu = self.process.cpu_percent(interval=None)
            rss = self.process.memory_info().rss
        except (psutil.Error, OSError):
            cpu, rss = 0, 0
        values.update(storage_bytes=disk_bytes, process_cpu_percent=cpu, process_rss_bytes=rss)
        return "\n".join(
            f"garage_watch_{name} {value}" for name, value in sorted(values.items())
        ) + "\n"
