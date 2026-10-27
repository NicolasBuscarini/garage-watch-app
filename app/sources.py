from typing import Protocol
import time

import cv2
import numpy as np

from app.models import Frame, utc_now


class VideoSource(Protocol):
    """Fornecedor de frames; não expõe tipos de câmera ao serviço de captura."""

    def open(self) -> None: ...
    def read(self) -> Frame | None: ...
    def close(self) -> None: ...


class OpenCVCameraSource:
    def __init__(self, device: str, width: int, height: int, fps: float):
        self.device, self.width, self.height, self.fps = device, width, height, fps
        self.capture: cv2.VideoCapture | None = None

    def open(self) -> None:
        device: int | str = int(self.device) if self.device.isdecimal() else self.device
        self.capture = cv2.VideoCapture(device)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.capture.set(cv2.CAP_PROP_FPS, self.fps)
        if not self.capture.isOpened():
            self.close()
            raise RuntimeError("não foi possível abrir a fonte de vídeo configurada")

    def read(self) -> Frame | None:
        if self.capture is None:
            return None
        ok, image = self.capture.read()
        return Frame(image, utc_now()) if ok and image is not None else None

    def close(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None


class SyntheticSource:
    """Fonte determinística para teste; nunca representa uma câmera física."""

    def __init__(self, width: int = 160, height: int = 120, fps: float = 10):
        self.width, self.height, self.fps = width, height, fps
        self.index = 0
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def read(self) -> Frame | None:
        if not self.opened:
            return None
        time.sleep(1 / self.fps)
        image = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        cv2.putText(image, str(self.index), (5, self.height // 2), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (255, 255, 255), 1)
        self.index += 1
        return Frame(image, utc_now())

    def close(self) -> None:
        self.opened = False


class IntermittentSyntheticSource(SyntheticSource):
    """Fonte contratual que interrompe leituras para exercitar reconexão local."""

    def __init__(self, width: int = 160, height: int = 120, fps: float = 10,
                 fail_after_frames: int = 0, failures: int = 0):
        super().__init__(width, height, fps)
        self.fail_after_frames = fail_after_frames
        self.failures_remaining = failures

    def read(self) -> Frame | None:
        if self.failures_remaining and self.index >= self.fail_after_frames:
            self.failures_remaining -= 1
            self.index += 1
            return None
        return super().read()
