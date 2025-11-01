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
import threading
from pathlib import Path
from threading import RLock
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from data_store import DataRecorder, DataStore
from q_sensor_lib import SensorController
from q_sensor_lib.errors import InvalidConfigValue, MenuTimeout, SerialIOError
from q_sensor_lib.models import ConnectionState

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# =============================================================================
# Global Singletons
# =============================================================================

_controller: Optional[SensorController] = None
_store: Optional[DataStore] = None
_recorder: Optional[DataRecorder] = None
_lock = RLock()  # Protects state-changing operations

# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="Q-Sensor API",
    description="REST and WebSocket interface for Biospherical Q-Series sensors",
    version="1.0.0"
)

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    port: str = Query(..., description="Serial port (e.g., /dev/ttyUSB0)"),
    baud: int = Query(9600, description="Baud rate")
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
    global _controller, _store, _lock

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
async def start_acquisition(poll_hz: float = Query(1.0, description="Poll rate for polled mode (Hz)")):
    """Start data acquisition.

    Exits menu and starts acquisition in configured mode.
    - Freerun: Device streams continuously
    - Polled: Controller queries at poll_hz rate

    Args:
        poll_hz: Polling frequency for polled mode (default 1.0 Hz)

    Returns:
        {"status": "started", "mode": "..."}

    Raises:
        503: If not connected or already acquiring
    """
    global _controller, _lock

    if not _controller:
        raise HTTPException(status_code=503, detail="Not connected")

    with _lock:
        if _controller.state in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
            raise HTTPException(status_code=400, detail="Acquisition already running")

        config = _controller.get_config()
        logger.info(f"Starting acquisition in {config.mode} mode...")

        _controller.start_acquisition(poll_hz=poll_hz)

        return {"status": "started", "mode": config.mode}


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
    global _controller, _store, _recorder, _lock

    with _lock:
        # Stop recorder if running
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


# =============================================================================
# Health Check
# =============================================================================

@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "service": "Q-Sensor API",
        "version": "1.0.0",
        "status": "online"
    }


# =============================================================================
# Startup/Shutdown Events
# =============================================================================

@app.on_event("startup")
async def startup_event():
    """Log startup."""
    logger.info("Q-Sensor API started")
    logger.info("CORS enabled for localhost:3000 and 127.0.0.1:3000")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    global _controller, _recorder

    logger.info("Shutting down Q-Sensor API...")

    # Stop recorder
    if _recorder and _recorder.is_running():
        logger.info("Stopping recorder...")
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
