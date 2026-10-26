from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from app.config import Settings
from app.runtime import CaptureRuntime
from app.sources import OpenCVCameraSource


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    source = OpenCVCameraSource(settings.camera_device, settings.width, settings.height, settings.fps)
    runtime = CaptureRuntime(settings, source)
    app.state.runtime = runtime
    runtime.start()
    try:
        yield
    finally:
        runtime.stop()


app = FastAPI(title="Garage Watch Capture", version="0.1.0", lifespan=lifespan)


@app.get("/health/live")
def liveness():
    return {"status": "live"}


@app.get("/health/ready")
def readiness():
    runtime = getattr(app.state, "runtime", None)
    if runtime is None or not runtime.started or not runtime.source_connected:
        raise HTTPException(status_code=503, detail="captura não inicializada")
    return {"status": "ready", "source": type(runtime.source).__name__}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="métricas indisponíveis")
    return PlainTextResponse(runtime.metrics.render(), media_type="text/plain; version=0.0.4")


@app.get("/api/v1/status")
def status():
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="captura não inicializada")
    return runtime.status()


@app.post("/api/v1/clips", status_code=202)
def create_clip():
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="captura não inicializada")
    try:
        job = runtime.trigger_clip()
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail="cota insuficiente para salvar o clipe") from exc
    return {**job.to_dict(), "message": "clipe solicitado; aguarda a janela pós-evento"}


@app.get("/api/v1/clips")
def list_clips():
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="captura não inicializada")
    return runtime.clips()
