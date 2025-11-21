"""FastAPI REST and WebSocket interface for Q-Sensor data acquisition.

Single-process, single-sensor lifecycle with thread-safe access to:
- SensorController (serial communication, config, acquisition)
- DataStore (pandas DataFrame storage)
- DataRecorder (background polling thread)

Error mapping:
- MenuTimeout → 504
- InvalidConfigValue → 400
- SerialIOError → 503
- Other exceptions → 500
"""

import asyncio
import logging
import os
import subprocess
import threading
from pathlib import Path
from threading import RLock
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from data_store import DataRecorder, DataStore
from data_store.session_manager import SessionManager
from data_store.store import ChunkedDataStore
from q_sensor_lib import SensorController
from q_sensor_lib.errors import InvalidConfigValue, MenuTimeout, SerialIOError
from q_sensor_lib.models import ConnectionState, Reading

# =============================================================================
# Environment Configuration
# =============================================================================

# Read configuration from environment variables
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "9150"))
DEFAULT_SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
DEFAULT_SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))
CHUNK_RECORDING_PATH = os.getenv("CHUNK_RECORDING_PATH", "/data/qsensor_recordings")
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://blueos.local,http://blueos.local:80,http://blueos.local:3000,http://blueos.local:9150"
).split(",")

# Version tracking
API_VERSION = "0.2.0"
try:
    GIT_COMMIT = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent.parent, stderr=subprocess.DEVNULL).decode().strip()
except Exception:
    GIT_COMMIT = "unknown"

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# =============================================================================
# Global Singletons
# =============================================================================

_controller: Optional[SensorController] = None
_store: Optional[DataStore] = None
_recorder: Optional[DataRecorder] = None
_chunked_store: Optional[ChunkedDataStore] = None  # DEPRECATED: kept for compatibility
_chunked_recorder: Optional[DataRecorder] = None  # DEPRECATED: kept for compatibility
_session_manager: Optional[SessionManager] = None  # NEW: manages recording sessions
_lock = RLock()  # Protects state-changing operations

# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="Q-Sensor API",
    description="REST and WebSocket interface for Biospherical Q-Series sensors",
    version="1.0.0"
)

# CORS for local development (configurable via CORS_ORIGINS env var)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 404 logging middleware for debugging
@app.middleware("http")
async def log_404_requests(request: Request, call_next):
    """Log all 404 responses to help debug missing routes."""
    response = await call_next(request)
    if response.status_code == 404:
        logger.warning(f"404 NOT FOUND: {request.method} {request.url.path} query={dict(request.query_params)}")
    return response

# Mount static files for webapp assets (CSS, JS, etc.)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "webapp" / "assets")), name="assets")
    logger.info(f"Static webapp assets mounted from {static_dir / 'webapp' / 'assets'}")

# =============================================================================
# Request/Response Models
# =============================================================================

class ConfigRequest(BaseModel):
    """Request body for POST /config."""
    averaging: Optional[int] = None
    adc_rate_hz: Optional[int] = None
    mode: Optional[Literal["freerun", "polled"]] = None
    tag: Optional[str] = None


class StatusResponse(BaseModel):
    """Response for GET /status."""
    connected: bool
    recording: bool
    sensor_id: str
    mode: Optional[str]
    rows: int
    state: str


class ConnectResponse(BaseModel):
    """Response for POST /connect."""
    status: str
    sensor_id: str


class RecordingStopResponse(BaseModel):
    """Response for POST /recording/stop."""
    status: str
    flush_path: Optional[str]


class StatsResponse(BaseModel):
    """Response for GET /sensor/stats."""
    row_count: int
    start_time: Optional[str]
    end_time: Optional[str]
    duration_s: Optional[float]
    est_sample_rate_hz: Optional[float]


# =============================================================================
# Chunked Recording Models (for live mirroring)
# =============================================================================

class RecordStartRequest(BaseModel):
    """Request for POST /record/start."""
    rate_hz: int = 500
    schema_version: int = 1
    mission: str = "Cockpit"
    roll_interval_s: float = 60.0


class RecordStartResponse(BaseModel):
    """Response for POST /record/start."""
    session_id: str
    started_at: str
    rate_hz: int
    schema_version: int


class RecordStopRequest(BaseModel):
    """Request for POST /record/stop."""
    session_id: str


class RecordStopResponse(BaseModel):
    """Response for POST /record/stop."""
    session_id: str
    stopped_at: str
    chunks: int
    rows: int


class RecordStatusResponse(BaseModel):
    """Response for GET /record/status."""
    session_id: str
    state: str  # "recording", "stopped"
    rows: int
    bytes: int
    last_chunk_index: int
    backlog: int  # chunks waiting to be downloaded


class ChunkMetadata(BaseModel):
    """Metadata for a single chunk."""
    index: int
    name: str
    size_bytes: int
    sha256: str
    rows: int


class SyncMarkerRequest(BaseModel):
    """Request for POST /record/sync-marker."""
    session_id: str
    sync_id: str
    marker_type: Literal["START", "STOP"]


class SyncMarkerResponse(BaseModel):
    """Response for POST /record/sync-marker."""
    status: str
    timestamp: str


class HealthResponse(BaseModel):
    """Response for GET /instrument/health."""
    connected: bool
    port: Optional[str]
    model: Optional[str]
    firmware: Optional[str]
    disk_free_bytes: Optional[int]


class TimeSyncResponse(BaseModel):
    """Response for GET /api/sync/time."""
    pi_iso: str
    pi_unix_ms: int
    container_version: str
    schema_version: int


# =============================================================================
# Exception Handlers
# =============================================================================

@app.exception_handler(MenuTimeout)
async def menu_timeout_handler(request, exc: MenuTimeout):
    """Map MenuTimeout to 504 Gateway Timeout."""
    logger.error(f"MenuTimeout: {exc}")
    raise HTTPException(status_code=504, detail=str(exc))


@app.exception_handler(InvalidConfigValue)
async def invalid_config_handler(request, exc: InvalidConfigValue):
    """Map InvalidConfigValue to 400 Bad Request."""
    logger.error(f"InvalidConfigValue: {exc}")
    raise HTTPException(status_code=400, detail=str(exc))


@app.exception_handler(SerialIOError)
async def serial_io_error_handler(request, exc: SerialIOError):
    """Map SerialIOError to 503 Service Unavailable."""
    logger.error(f"SerialIOError: {exc}")
    raise HTTPException(status_code=503, detail=str(exc))


# =============================================================================
# Read-Only Endpoints (Low Latency)
# =============================================================================

@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Get current system status.

    Returns connection state, recording status, sensor ID, mode, and row count.
    Thread-safe read operation.
    """
    global _controller, _store, _recorder

    connected = _controller is not None and _controller.is_connected()
    recording = _recorder is not None and _recorder.is_running()
    sensor_id = _controller.sensor_id if _controller else "unknown"
    mode = None
    rows = 0
    state_str = "disconnected"

    if _controller:
        state_str = _controller.state.value
        config = None
        try:
            if _controller.state == ConnectionState.CONFIG_MENU:
                config = _controller.get_config()
        except Exception:
            pass  # Ignore if not in menu

        if config:
            mode = config.mode

    if _store:
        df = _store.get_dataframe()
        rows = len(df)

    return StatusResponse(
        connected=connected,
        recording=recording,
        sensor_id=sensor_id,
        mode=mode,
        rows=rows,
        state=state_str
    )


@app.get("/latest")
async def get_latest():
    """Get the most recent reading.

    Returns dict with schema keys or empty {} if no data.
    Thread-safe read via DataStore.
    """
    global _store

    if not _store:
        return {}

    latest = _store.get_latest()
    return latest if latest else {}


@app.get("/recent")
async def get_recent(seconds: int = Query(60, ge=1, le=300)):
    """Get recent readings from last N seconds (capped at 300s).

    Args:
        seconds: Time window in seconds (1-300)

    Returns:
        {"rows": [...]} with list of reading dicts

    Thread-safe read via DataStore.
    """
    global _store

    if not _store:
        return {"rows": []}

    # Cap at 300 seconds
    seconds = min(seconds, 300)

    recent_df = _store.get_recent(seconds=seconds)
    rows = recent_df.to_dict(orient="records")

    return {"rows": rows}


# =============================================================================
# Config & Lifecycle Endpoints
# =============================================================================

@app.post("/connect", response_model=ConnectResponse)
async def connect(
    port: str = Query(DEFAULT_SERIAL_PORT, description="Serial port (e.g., /dev/ttyUSB0)"),
    baud: int = Query(DEFAULT_SERIAL_BAUD, description="Baud rate")
):
    """Connect to sensor and enter config menu.

    Opens serial port, sends ESC to enter menu, reads current config.

    Args:
        port: Serial port path
        baud: Baud rate (default 9600)

    Returns:
        {"status": "connected", "sensor_id": "..."}

    Raises:
        503: If port cannot be opened (SerialIOError)
        504: If menu does not appear (MenuTimeout)
    """
    global _controller, _store, _session_manager, _lock

    with _lock:
        if _controller is not None:
            raise HTTPException(
                status_code=400,
                detail="Already connected. Disconnect first."
            )

        logger.info(f"Connecting to {port} at {baud} baud...")
        _controller = SensorController()
        _store = DataStore(max_rows=100000)  # 100k rows default

        _controller.connect(port=port, baud=baud)

        # Initialize SessionManager with connected controller
        _session_manager = SessionManager(
            controller=_controller,
            base_path=Path(CHUNK_RECORDING_PATH)
        )

        logger.info(f"Connected to sensor: {_controller.sensor_id}")
        return ConnectResponse(
            status="connected",
            sensor_id=_controller.sensor_id
        )


@app.post("/config")
async def set_config(config: Optional[ConfigRequest] = None):
    """Apply configuration changes.

    Only provided fields are updated. Sensor must be in CONFIG_MENU state.

    Args:
        config: Optional body with averaging, adc_rate_hz, mode, tag

    Returns:
        Updated configuration as dict

    Raises:
        400: If values are invalid (InvalidConfigValue)
        503: If not connected (SerialIOError)
        504: If menu timeout (MenuTimeout)
    """
    global _controller, _lock

    if not _controller:
        raise HTTPException(status_code=503, detail="Not connected")

    with _lock:
        updated_config = None

        if config:
            # Apply each provided field
            if config.averaging is not None:
                logger.info(f"Setting averaging to {config.averaging}")
                updated_config = _controller.set_averaging(config.averaging)

            if config.adc_rate_hz is not None:
                logger.info(f"Setting ADC rate to {config.adc_rate_hz} Hz")
                updated_config = _controller.set_adc_rate(config.adc_rate_hz)

            if config.mode is not None:
                logger.info(f"Setting mode to {config.mode}")
                updated_config = _controller.set_mode(config.mode, tag=config.tag)

        # Return current config
        if not updated_config:
            updated_config = _controller.get_config()

        return {
            "averaging": updated_config.averaging,
            "adc_rate_hz": updated_config.adc_rate_hz,
            "mode": updated_config.mode,
            "tag": updated_config.tag,
            "sample_period_s": updated_config.sample_period_s,
        }


@app.post("/start")
async def start_acquisition(
    poll_hz: float = Query(1.0, description="Poll rate for polled mode (Hz)"),
    auto_record: bool = Query(False, description="Automatically start DataRecorder")
):
    """Start data acquisition.

    Exits menu and starts acquisition in configured mode.
    - Freerun: Device streams continuously
    - Polled: Controller queries at poll_hz rate

    Args:
        poll_hz: Polling frequency for polled mode (default 1.0 Hz)
        auto_record: Automatically start DataRecorder to store readings (default True)

    Returns:
        {"status": "started", "mode": "...", "recording": bool}

    Raises:
        503: If not connected or already acquiring
    """
    global _controller, _store, _recorder, _lock

    logger.info(f"[SENSOR/START] Request: poll_hz={poll_hz}, auto_record={auto_record}")

    if not _controller:
        logger.error("[SENSOR/START] FAILED: Not connected")
        raise HTTPException(status_code=503, detail="Not connected")

    with _lock:
        if _controller.state in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
            logger.error(f"[SENSOR/START] FAILED: Acquisition already running in {_controller.state.value}")
            raise HTTPException(status_code=400, detail="Acquisition already running")

        config = _controller.get_config()
        logger.info(f"[SENSOR/START] Starting acquisition: mode={config.mode}, poll_hz={poll_hz}")

        _controller.start_acquisition(poll_hz=poll_hz)

        # Auto-start DataRecorder if requested (default True)
        recording = False
        if auto_record:
            if not _store:
                logger.info("[SENSOR/START] Creating DataStore: max_rows=100000")
                _store = DataStore(max_rows=100000, auto_flush_interval_s=None)

            if _recorder is None or not _recorder.is_running():
                logger.info("[SENSOR/START] Auto-starting DataRecorder: poll_interval=0.2s")
                _recorder = DataRecorder(_controller, _store, poll_interval_s=0.2)
                _recorder.start()
                recording = True

        logger.info(f"[SENSOR/START] SUCCESS: mode={config.mode}, recording={recording}")
        return {"status": "started", "mode": config.mode, "recording": recording}


@app.post("/recording/start")
async def start_recording(
    poll_interval_s: float = Query(0.2, description="Recorder poll interval (seconds)"),
    auto_flush_interval_s: Optional[float] = Query(None, description="Auto-flush interval (seconds)"),
    max_rows: int = Query(100000, description="Max rows in memory")
):
    """Start DataRecorder to poll controller buffer and write to DataFrame.

    Must be called after /start (acquisition must be running).

    Args:
        poll_interval_s: How often recorder polls controller buffer (default 0.2s)
        auto_flush_interval_s: Optional auto-flush interval (default None)
        max_rows: Max DataFrame rows (default 100000)

    Returns:
        {"status": "recording"}

    Raises:
        400: If already recording or acquisition not started
        503: If not connected
    """
    global _controller, _store, _recorder, _lock

    if not _controller:
        raise HTTPException(status_code=503, detail="Not connected")

    if not _store:
        # Create store if not exists (shouldn't happen if connected properly)
        _store = DataStore(
            max_rows=max_rows,
            auto_flush_interval_s=auto_flush_interval_s
        )

    with _lock:
        if _recorder is not None and _recorder.is_running():
            raise HTTPException(status_code=400, detail="Already recording")

        if _controller.state not in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
            raise HTTPException(
                status_code=400,
                detail="Acquisition not running. Call /start first."
            )

        logger.info(f"Starting recorder (poll_interval={poll_interval_s}s)...")
        _recorder = DataRecorder(_controller, _store, poll_interval_s=poll_interval_s)
        _recorder.start()

        return {"status": "recording"}


@app.post("/recording/stop", response_model=RecordingStopResponse)
async def stop_recording(
    flush: Literal["csv", "parquet", "none"] = Query("none", description="Export format")
):
    """Stop DataRecorder and optionally flush to disk.

    Args:
        flush: Export format ("csv", "parquet", or "none")

    Returns:
        {"status": "stopped", "flush_path": "..." or null}

    Raises:
        400: If recorder not running
    """
    global _recorder, _lock

    with _lock:
        if not _recorder or not _recorder.is_running():
            raise HTTPException(status_code=400, detail="Recorder not running")

        flush_path = None
        if flush != "none":
            logger.info(f"Stopping recorder with flush format: {flush}")
            flush_path = _recorder.stop(flush_format=flush)
        else:
            logger.info("Stopping recorder without flush")
            _recorder.stop()

        return RecordingStopResponse(
            status="stopped",
            flush_path=flush_path
        )


@app.post("/stop")
async def stop_acquisition():
    """Stop acquisition and return to CONFIG_MENU.

    CRITICAL: If recorder is running, stops it first to prevent data loss.

    Returns:
        {"status": "stopped"}

    Raises:
        503: If not connected or not acquiring
    """
    global _controller, _recorder, _lock

    if not _controller:
        raise HTTPException(status_code=503, detail="Not connected")

    with _lock:
        # Defensive: Stop recorder first if running
        if _recorder and _recorder.is_running():
            logger.warning("Recorder still running during /stop - stopping it first")
            _recorder.stop()

        if _controller.state not in (
            ConnectionState.ACQ_FREERUN,
            ConnectionState.ACQ_POLLED,
            ConnectionState.PAUSED
        ):
            raise HTTPException(status_code=400, detail="Acquisition not running")

        logger.info("Stopping acquisition...")
        _controller.stop()

        return {"status": "stopped"}


@app.post("/disconnect")
async def disconnect():
    """Disconnect from sensor and clean up resources.

    Stops acquisition and recorder if running, closes serial port.

    Returns:
        {"status": "disconnected"}
    """
    global _controller, _store, _recorder, _session_manager, _lock

    with _lock:
        # Stop all active recording sessions
        if _session_manager:
            logger.info("Shutting down all recording sessions before disconnect...")
            _session_manager.shutdown_all()
            _session_manager = None

        # Stop recorder if running (old system)
        if _recorder and _recorder.is_running():
            logger.info("Stopping recorder before disconnect...")
            _recorder.stop()
            _recorder = None

        # Stop controller if connected
        if _controller:
            if _controller.is_connected():
                if _controller.state in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
                    logger.info("Stopping acquisition before disconnect...")
                    _controller.stop()

                logger.info("Disconnecting from sensor...")
                _controller.disconnect()

            _controller = None

        # Clear store
        _store = None

        return {"status": "disconnected"}


@app.post("/pause")
async def pause_acquisition():
    """Pause acquisition (enter PAUSED state).

    Sends ESC to pause data stream and enter pause menu.
    Can be resumed with POST /resume.

    Returns:
        {"status": "paused"}

    Raises:
        503: If not connected or not in acquisition state
    """
    global _controller, _lock

    if not _controller:
        raise HTTPException(status_code=503, detail="Not connected")

    with _lock:
        if _controller.state not in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
            raise HTTPException(
                status_code=400,
                detail="Acquisition not running. Cannot pause."
            )

        logger.info("Pausing acquisition...")
        _controller.pause()

        return {"status": "paused"}


@app.post("/resume")
async def resume_acquisition():
    """Resume acquisition from PAUSED state.

    Exits pause menu and resumes data streaming in previous mode.

    Returns:
        {"status": "resumed", "mode": "..."}

    Raises:
        503: If not connected or not in PAUSED state
    """
    global _controller, _lock

    if not _controller:
        raise HTTPException(status_code=503, detail="Not connected")

    with _lock:
        if _controller.state != ConnectionState.PAUSED:
            raise HTTPException(
                status_code=400,
                detail="Not in paused state. Cannot resume."
            )

        logger.info("Resuming acquisition...")
        _controller.resume()

        # Get mode after resuming
        mode = "freerun" if _controller.state == ConnectionState.ACQ_FREERUN else "polled"

        return {"status": "resumed", "mode": mode}


# =============================================================================
# Data Statistics & Export Endpoints
# =============================================================================

@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Get statistics about collected data.

    Returns row count, time range, duration, and estimated sample rate.
    Thread-safe read via DataStore.

    Returns:
        StatsResponse with row_count, start_time, end_time, duration_s, est_sample_rate_hz
    """
    global _store

    if not _store:
        return StatsResponse(
            row_count=0,
            start_time=None,
            end_time=None,
            duration_s=None,
            est_sample_rate_hz=None
        )

    stats = _store.get_stats()

    return StatsResponse(
        row_count=stats["row_count"],
        start_time=stats["start_time"],
        end_time=stats["end_time"],
        duration_s=stats["duration_s"],
        est_sample_rate_hz=stats["est_sample_rate_hz"]
    )


@app.get("/export/csv")
async def export_csv():
    """Export current data to CSV file without stopping recorder.

    Creates a timestamped CSV file in the current directory.

    Returns:
        FileResponse with CSV download

    Raises:
        400: If no data available
    """
    global _store

    if not _store:
        raise HTTPException(status_code=400, detail="No data store available")

    df = _store.get_dataframe()
    if len(df) == 0:
        raise HTTPException(status_code=400, detail="No data to export")

    # Export to CSV
    logger.info("Exporting data to CSV...")
    csv_path = _store.export_csv()

    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=500, detail="Failed to export CSV")

    return FileResponse(
        path=csv_path,
        media_type="text/csv",
        filename=Path(csv_path).name
    )


@app.get("/export/parquet")
async def export_parquet():
    """Export current data to Parquet file without stopping recorder.

    Creates a timestamped Parquet file in the current directory.

    Returns:
        FileResponse with Parquet download

    Raises:
        400: If no data available
    """
    global _store

    if not _store:
        raise HTTPException(status_code=400, detail="No data store available")

    df = _store.get_dataframe()
    if len(df) == 0:
        raise HTTPException(status_code=400, detail="No data to export")

    # Export to Parquet
    logger.info("Exporting data to Parquet...")
    parquet_path = _store.export_parquet()

    if not parquet_path or not Path(parquet_path).exists():
        raise HTTPException(status_code=500, detail="Failed to export Parquet")

    return FileResponse(
        path=parquet_path,
        media_type="application/octet-stream",
        filename=Path(parquet_path).name
    )


# =============================================================================
# /sensor/* Aliases (for API compatibility)
# =============================================================================

# Add aliases with /sensor prefix for all main endpoints
app.add_api_route("/sensor/status", get_status, methods=["GET"], response_model=StatusResponse)
app.add_api_route("/sensor/latest", get_latest, methods=["GET"])
app.add_api_route("/sensor/recent", get_recent, methods=["GET"])
app.add_api_route("/sensor/stats", get_stats, methods=["GET"], response_model=StatsResponse)
app.add_api_route("/sensor/connect", connect, methods=["POST"], response_model=ConnectResponse)
app.add_api_route("/sensor/config", set_config, methods=["POST"])
app.add_api_route("/sensor/start", start_acquisition, methods=["POST"])
app.add_api_route("/sensor/pause", pause_acquisition, methods=["POST"])
app.add_api_route("/sensor/resume", resume_acquisition, methods=["POST"])
app.add_api_route("/sensor/stop", stop_acquisition, methods=["POST"])

# Add recording export aliases
app.add_api_route("/recording/export/csv", export_csv, methods=["GET"])
app.add_api_route("/recording/export/parquet", export_parquet, methods=["GET"])


# =============================================================================
# WebSocket Streaming
# =============================================================================

@app.websocket("/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket endpoint for real-time streaming of latest readings.

    Sends JSON updates every 100-250ms (10-15 Hz) with latest reading.
    Client receives messages with schema keys: timestamp, sensor_id, mode, value, TempC, Vin.

    Usage:
        ws = new WebSocket("ws://localhost:8000/stream");
        ws.onmessage = (event) => {
            const reading = JSON.parse(event.data);
            console.log(reading.value, reading.timestamp);
        };
    """
    global _store

    await websocket.accept()
    logger.info(f"WebSocket client connected: {websocket.client}")

    if not _store:
        await websocket.send_json({"error": "No data store available"})
        await websocket.close()
        return

    try:
        last_ts = None

        while True:
            # Get latest reading
            latest = _store.get_latest()

            if latest:
                # Only send if timestamp changed (new reading)
                current_ts = latest.get("timestamp")
                if current_ts != last_ts:
                    await websocket.send_json(latest)
                    last_ts = current_ts

            # Wait 100ms before next check (10 Hz)
            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: {websocket.client}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass


# WebSocket alias for /sensor/stream
@app.websocket("/sensor/stream")
async def websocket_stream_alias(websocket: WebSocket):
    """Alias for /stream with /sensor prefix."""
    await websocket_stream(websocket)


# =============================================================================
# Chunked Recording Endpoints (for live mirroring)
# =============================================================================

@app.post("/record/start", response_model=RecordStartResponse)
async def start_chunked_recording(req: RecordStartRequest):
    """Start a new chunked recording session for live mirroring.

    Creates a session directory, initializes ChunkedDataStore, starts acquisition
    and recording. Chunks are written atomically (.tmp → .csv) and tracked in manifest.json.

    Args:
        req: RecordStartRequest with rate_hz, schema_version, mission, roll_interval_s

    Returns:
        RecordStartResponse with session_id, started_at, rate_hz, schema_version

    Raises:
        424: If sensor not connected
        409: If recording already active
        507: If insufficient disk space
    """
    import shutil

    global _controller, _session_manager, _lock

    logger.info(f"[RECORD/START] Request received: mission={req.mission}, rate_hz={req.rate_hz}, "
                f"schema_version={req.schema_version}, roll_interval_s={req.roll_interval_s}")

    # Check sensor connected
    if not _controller or not _controller.is_connected():
        logger.error("[RECORD/START] FAILED: Sensor not connected")
        raise HTTPException(status_code=424, detail="Sensor not connected. Call /connect first.")

    # Check session manager initialized
    if not _session_manager:
        logger.error("[RECORD/START] FAILED: SessionManager not initialized")
        raise HTTPException(status_code=500, detail="SessionManager not initialized. Call /connect first.")

    # Check disk space (require at least 100MB free)
    try:
        disk_usage = shutil.disk_usage(CHUNK_RECORDING_PATH)
        logger.info(f"[RECORD/START] Disk check: free={disk_usage.free / (1024**3):.2f}GB, path={CHUNK_RECORDING_PATH}")
        if disk_usage.free < 100 * 1024 * 1024:
            logger.error(f"[RECORD/START] FAILED: Insufficient disk space ({disk_usage.free / (1024**2):.1f}MB)")
            raise HTTPException(status_code=507, detail="Insufficient disk space (< 100MB free)")
    except Exception as e:
        logger.warning(f"[RECORD/START] Could not check disk space: {e}")

    with _lock:
        # Start acquisition if not already running
        if _controller.state not in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
            logger.info(f"[RECORD/START] Auto-starting acquisition: mode={_controller.state.value}")
            _controller.start_acquisition(poll_hz=req.rate_hz / 500.0)  # Approx poll rate
        else:
            logger.info(f"[RECORD/START] Acquisition already running: mode={_controller.state.value}")

        try:
            # Create and start recording session via SessionManager
            session = _session_manager.create_session(
                mission=req.mission,
                rate_hz=req.rate_hz,
                schema_version=req.schema_version,
                roll_interval_s=req.roll_interval_s,
                target_chunk_mb=2.0
            )

            logger.info(f"[RECORD/START] SUCCESS: session={session.session_id}, rate={req.rate_hz}Hz, "
                        f"mission={req.mission}, recording_path={CHUNK_RECORDING_PATH}/{session.session_id}")

            return RecordStartResponse(
                session_id=session.session_id,
                started_at=session.started_at.isoformat(),
                rate_hz=session.rate_hz,
                schema_version=session.schema_version
            )

        except RuntimeError as e:
            # Session already active
            logger.error(f"[RECORD/START] FAILED: {e}")
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            logger.error(f"[RECORD/START] FAILED: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to start recording: {e}")


@app.post("/record/stop", response_model=RecordStopResponse)
async def stop_chunked_recording(req: RecordStopRequest):
    """Stop chunked recording session and finalize all chunks.

    Args:
        req: RecordStopRequest with session_id

    Returns:
        RecordStopResponse with session_id, stopped_at, chunks, rows

    Raises:
        404: If session not found
        400: If session is not in RECORDING state
    """
    global _session_manager, _lock

    logger.info(f"[RECORD/STOP] Request received: session_id={req.session_id}")

    # Check session manager initialized
    if not _session_manager:
        logger.error("[RECORD/STOP] FAILED: SessionManager not initialized")
        raise HTTPException(status_code=500, detail="SessionManager not initialized")

    with _lock:
        # Get session and validate it exists
        session = _session_manager.get_session(req.session_id)
        if not session:
            logger.error(f"[RECORD/STOP] FAILED: Session not found: {req.session_id}")
            raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")

        # Validate session is recording
        if not session.is_recording:
            logger.error(f"[RECORD/STOP] FAILED: Session not recording (state={session.state.value})")
            raise HTTPException(
                status_code=400,
                detail=f"Session is not recording (state={session.state.value})"
            )

        try:
            logger.info(f"[RECORD/STOP] Stopping session {req.session_id}...")

            # Stop session via SessionManager
            stats = _session_manager.stop_session(req.session_id)

            logger.info(
                f"[RECORD/STOP] SUCCESS: session={req.session_id}, "
                f"chunks={stats.chunk_count}, rows={stats.total_rows}, bytes={stats.total_bytes}"
            )

            return RecordStopResponse(
                session_id=req.session_id,
                stopped_at=stats.stopped_at.isoformat(),
                chunks=stats.chunk_count,
                rows=stats.total_rows
            )

        except Exception as e:
            logger.error(f"[RECORD/STOP] FAILED: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to stop recording: {e}")


@app.post("/record/sync-marker", response_model=SyncMarkerResponse)
async def inject_sync_marker(req: SyncMarkerRequest):
    """Inject a sync marker into the active recording buffer."""
    global _session_manager, _lock

    logger.info(f"[RECORD/SYNC-MARKER] Request: session_id={req.session_id}, type={req.marker_type}")

    if not _session_manager:
        logger.error("[RECORD/SYNC-MARKER] FAILED: SessionManager not initialized")
        raise HTTPException(status_code=500, detail="SessionManager not initialized")

    with _lock:
        session = _session_manager.get_session(req.session_id)
        if not session:
            logger.error(f"[RECORD/SYNC-MARKER] FAILED: Session not found: {req.session_id}")
            raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")

        if not session.is_recording:
            logger.error(f"[RECORD/SYNC-MARKER] FAILED: Session not recording (state={session.state.value})")
            raise HTTPException(
                status_code=400,
                detail=f"Session is not recording (state={session.state.value})"
            )

        # Build marker reading
        marker_ts = datetime.now(timezone.utc)
        marker_value = int(req.sync_id[:8], 16) if req.sync_id else 0
        marker_mode = f"SYNC_{req.marker_type}"

        marker_reading = Reading(
            ts=marker_ts,
            sensor_id=session.sensor_id,
            mode=marker_mode,  # Special marker mode (preserved in CSV)
            data={
                "value": marker_value,
                "TempC": 0.0,
                "Vin": 0.0,
            },
        )

        try:
            timestamp = session.inject_sync_marker(marker_reading)
            logger.info(
                f"[RECORD/SYNC-MARKER] Injected {marker_mode} "
                f"(sync_id={req.sync_id[:8]}..., sensor={session.sensor_id})"
            )
            return SyncMarkerResponse(status="injected", timestamp=timestamp)
        except Exception as e:
            logger.error(f"[RECORD/SYNC-MARKER] FAILED to inject marker: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to inject marker: {e}")


@app.get("/record/status", response_model=RecordStatusResponse)
async def get_recording_status(session_id: str = Query(...)):
    """Get current recording session status.

    Args:
        session_id: Session UUID

    Returns:
        RecordStatusResponse with state, rows, bytes, last_chunk_index, backlog

    Raises:
        404: If session not found
    """
    global _session_manager

    logger.debug(f"[RECORD/STATUS] Request: session_id={session_id}")

    # Check session manager initialized
    if not _session_manager:
        logger.error("[RECORD/STATUS] FAILED: SessionManager not initialized")
        raise HTTPException(status_code=500, detail="SessionManager not initialized")

    # Get session and validate it exists
    session = _session_manager.get_session(session_id)
    if not session:
        logger.error(f"[RECORD/STATUS] FAILED: Session not found: {session_id}")
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    # Get session status
    status = session.get_status()

    # Map SessionState enum to string for API response
    state_str = "recording" if status.state.value == "recording" else "stopped"

    logger.debug(f"[RECORD/STATUS] session={session_id}, state={state_str}, rows={status.rows}, "
                 f"bytes={status.bytes}, chunks={status.backlog}")

    return RecordStatusResponse(
        session_id=session_id,
        state=state_str,
        rows=status.rows,
        bytes=status.bytes,
        last_chunk_index=status.last_chunk_index,
        backlog=status.backlog
    )


@app.get("/record/snapshots", response_model=list[ChunkMetadata])
async def get_chunk_snapshots(session_id: str = Query(...)):
    """Get list of finalized chunks with metadata (size, SHA256).

    Args:
        session_id: Session UUID

    Returns:
        List of ChunkMetadata with index, name, size_bytes, sha256, rows

    Raises:
        404: If session not found
    """
    global _session_manager

    logger.debug(f"[RECORD/SNAPSHOTS] Request: session_id={session_id}")

    # Check session manager initialized
    if not _session_manager:
        logger.error("[RECORD/SNAPSHOTS] FAILED: SessionManager not initialized")
        raise HTTPException(status_code=500, detail="SessionManager not initialized")

    # Get session and validate it exists
    session = _session_manager.get_session(session_id)
    if not session:
        logger.error(f"[RECORD/SNAPSHOTS] FAILED: Session not found: {session_id}")
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    # Get snapshots from session
    try:
        chunks = session.get_snapshots()
        logger.debug(f"[RECORD/SNAPSHOTS] Returning {len(chunks)} chunks for session {session_id}")
        return [ChunkMetadata(**chunk) for chunk in chunks]
    except RuntimeError as e:
        logger.error(f"[RECORD/SNAPSHOTS] FAILED: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files/{session_id}/{filename}")
async def download_chunk_file(session_id: str, filename: str):
    """Download a finalized chunk file.

    Args:
        session_id: Session UUID
        filename: Chunk filename (e.g., "chunk_00000.csv")

    Returns:
        FileResponse with CSV chunk

    Raises:
        404: If session or file not found
    """
    global _session_manager

    # Try to get chunk from session manager first
    if _session_manager:
        session = _session_manager.get_session(session_id)
        if session:
            try:
                chunk_path = session.get_chunk_path(filename)
                logger.debug(f"[FILES] Serving chunk {filename} from active session {session_id}")

                return FileResponse(
                    path=str(chunk_path),
                    media_type="text/csv",
                    filename=filename
                )
            except (RuntimeError, FileNotFoundError) as e:
                logger.debug(f"[FILES] Chunk not found in active session: {e}")
                # Fall through to recovery mode below

    # Recovery mode: allow download even if no active session (for stopped sessions)
    logger.debug(f"[FILES] Attempting recovery mode for session {session_id}")
    session_dir = Path(CHUNK_RECORDING_PATH) / session_id
    if not session_dir.exists():
        logger.error(f"[FILES] Session directory not found: {session_dir}")
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    chunk_path = session_dir / filename
    if not chunk_path.exists():
        logger.error(f"[FILES] Chunk file not found: {chunk_path}")
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    logger.debug(f"[FILES] Serving chunk {filename} from stopped session {session_id} (recovery mode)")

    return FileResponse(
        path=str(chunk_path),
        media_type="text/csv",
        filename=filename
    )


@app.get("/instrument/health", response_model=HealthResponse)
async def get_instrument_health():
    """Get instrument connection health and disk space.

    Returns:
        HealthResponse with connected, port, model, firmware, disk_free_bytes
    """
    import shutil

    global _controller

    connected = _controller is not None and _controller.is_connected()
    port = None
    model = None
    firmware = None
    disk_free_bytes = None

    if _controller and connected:
        port = DEFAULT_SERIAL_PORT  # Simplified: use default
        model = _controller.sensor_id
        firmware = "unknown"  # Not available in current API

    # Check disk space
    try:
        disk_usage = shutil.disk_usage(CHUNK_RECORDING_PATH)
        disk_free_bytes = disk_usage.free
    except Exception as e:
        logger.warning(f"Could not check disk space: {e}")

    return HealthResponse(
        connected=connected,
        port=port,
        model=model,
        firmware=firmware,
        disk_free_bytes=disk_free_bytes
    )


@app.get("/events")
async def events_stream(session_id: str = Query(...)):
    """Server-Sent Events stream for recording progress (SSE).

    NOT IMPLEMENTED in MVP. Returns 501 Not Implemented.
    Future: Stream state, bytes, last_chunk, errors in real-time.
    """
    raise HTTPException(status_code=501, detail="SSE stream not implemented in MVP")


# =============================================================================
# Health Check
# =============================================================================

@app.get("/")
async def root():
    """Serve the React webapp."""
    webapp_index = Path(__file__).parent / "static" / "webapp" / "index.html"
    if webapp_index.exists():
        return FileResponse(webapp_index)
    else:
        # Fallback health check if webapp not found
        return {
            "service": "Q-Sensor API",
            "version": "1.0.0",
            "status": "online"
        }

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "service": "Q-Sensor API",
        "version": "1.0.0",
        "status": "online"
    }


@app.get("/version")
async def version():
    """Version tracking endpoint for debugging and compatibility checks."""
    return {
        "api": API_VERSION,
        "git": GIT_COMMIT,
        "status": "online"
    }


@app.get("/docs.json")
async def docs_json_redirect():
    """Redirect legacy /docs.json to /openapi.json for BlueOS compatibility."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/openapi.json")


@app.get("/register_service")
async def register_service():
    """BlueOS service discovery endpoint."""
    return {
        "name": "Q-Sensor API",
        "description": "Q-Series sensor data acquisition and control",
        "icon": "mdi-chart-line",
        "company": "Biospherical Instruments Inc.",
        "version": "1.0.0",
        "webpage": "/",
        "api": "/docs"
    }


# =============================================================================
# Time Synchronization
# =============================================================================

@app.get("/api/sync/time", response_model=TimeSyncResponse)
async def get_sync_time():
    """Get Pi current time for clock offset measurement.

    Returns Pi time in ISO 8601 and Unix milliseconds format.
    Used by topside Cockpit to measure clock offset via HTTP round-trip.

    Returns:
        TimeSyncResponse with pi_iso, pi_unix_ms, container_version, schema_version
    """
    now = datetime.now(timezone.utc)
    pi_iso = now.isoformat()
    pi_unix_ms = int(now.timestamp() * 1000)

    return TimeSyncResponse(
        pi_iso=pi_iso,
        pi_unix_ms=pi_unix_ms,
        container_version=API_VERSION,
        schema_version=1
    )


# =============================================================================
# Startup/Shutdown Events
# =============================================================================

@app.on_event("startup")
async def startup_event():
    """Log startup and configuration, including route table dump."""
    logger.info("=" * 60)
    logger.info("Q-Sensor API started")
    logger.info(f"Version: {API_VERSION}")
    logger.info(f"Git Commit: {GIT_COMMIT}")
    logger.info(f"Host: {API_HOST}")
    logger.info(f"Port: {API_PORT}")
    logger.info(f"Default Serial Port: {DEFAULT_SERIAL_PORT}")
    logger.info(f"Default Serial Baud: {DEFAULT_SERIAL_BAUD}")
    logger.info(f"Chunk Recording Path: {CHUNK_RECORDING_PATH}")
    logger.info(f"CORS Origins: {CORS_ORIGINS}")
    logger.info(f"Log Level: {LOG_LEVEL}")
    logger.info("=" * 60)

    # Dump all registered routes for debugging
    logger.info("=== REGISTERED ROUTES ===")
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            methods = ','.join(sorted(route.methods))
            logger.info(f"{methods:10} {route.path}")
        elif hasattr(route, 'path'):
            logger.info(f"{'WS':10} {route.path}")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    global _controller, _recorder, _session_manager

    logger.info("Shutting down Q-Sensor API...")

    # Stop all recording sessions first (most important for data integrity)
    if _session_manager:
        logger.info("Shutting down all recording sessions...")
        try:
            _session_manager.shutdown_all()
        except Exception as e:
            logger.error(f"Error shutting down sessions: {e}", exc_info=True)

    # Stop old-style recorder (if still in use)
    if _recorder and _recorder.is_running():
        logger.info("Stopping old-style recorder...")
        _recorder.stop()

    # Disconnect controller
    if _controller and _controller.is_connected():
        logger.info("Disconnecting controller...")
        try:
            if _controller.state in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
                _controller.stop()
            _controller.disconnect()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    logger.info("Shutdown complete")
