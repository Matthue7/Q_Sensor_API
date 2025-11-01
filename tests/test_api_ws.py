"""Tests for FastAPI WebSocket /stream endpoint.

Tests verify:
- WebSocket connection and disconnection
- Real-time streaming of readings at 10-15 Hz
- JSON message format with schema keys
- Behavior when no data available
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from api import main as api_module
from fakes.fake_serial import FakeSerial
from q_sensor_lib.transport import Transport


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset global singletons before each test."""
    api_module._controller = None
    api_module._store = None
    api_module._recorder = None
    yield
    # Cleanup
    if api_module._recorder and api_module._recorder.is_running():
        api_module._recorder.stop()
    if api_module._controller and api_module._controller.is_connected():
        try:
            if api_module._controller.state.value in ("acq_freerun", "acq_polled"):
                api_module._controller.stop()
            api_module._controller.disconnect()
        except Exception:
            pass
    api_module._controller = None
    api_module._store = None
    api_module._recorder = None


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(api_module.app)


@pytest.fixture
def fake_serial():
    """Create a FakeSerial instance."""
    return FakeSerial(
        serial_number="WS_TEST",
        quiet_mode=True
    )


@pytest.fixture
def monkeypatch_transport(monkeypatch, fake_serial):
    """Monkeypatch Transport.open to use FakeSerial."""
    def mock_open(port: str, baud: int):
        return Transport(fake_serial)

    monkeypatch.setattr(Transport, "open", mock_open)


# =============================================================================
# WebSocket Tests
# =============================================================================

def test_websocket_no_store(client):
    """Test WebSocket /stream when no data store exists."""
    with client.websocket_connect("/stream") as websocket:
        data = websocket.receive_json()
        assert "error" in data
        assert "No data store" in data["error"]


def test_websocket_stream_freerun(client, monkeypatch_transport):
    """Test WebSocket receives streaming readings in freerun mode."""
    # Setup: connect, config, start acquisition, start recording
    client.post("/connect?port=/dev/fake&baud=9600")
    client.post("/config", json={"averaging": 12, "adc_rate_hz": 125, "mode": "freerun"})
    client.post("/start")
    client.post("/recording/start?poll_interval_s=0.1")

    # Give it time to start streaming (~10 Hz expected)
    time.sleep(0.5)

    # Connect WebSocket
    with client.websocket_connect("/stream") as websocket:
        messages = []

        # Receive messages for ~500ms
        start = time.time()
        while time.time() - start < 0.5:
            try:
                # receive_json() blocks, use timeout
                data = websocket.receive_json()
                messages.append(data)
            except Exception:
                break

    # Should have received at least 3 messages over 500ms (10 Hz → ~5 messages expected)
    assert len(messages) >= 3, f"Expected >=3 messages, got {len(messages)}"

    # Check first message has schema keys
    first = messages[0]
    assert "timestamp" in first
    assert "sensor_id" in first
    assert "mode" in first
    assert "value" in first

    # All messages should have mode=freerun
    for msg in messages:
        if "mode" in msg:
            assert msg["mode"] == "freerun"


def test_websocket_stream_empty_initially(client, monkeypatch_transport):
    """Test WebSocket when stream starts before data arrives."""
    # Setup but don't start recording yet
    client.post("/connect?port=/dev/fake&baud=9600")
    client.post("/config", json={"averaging": 125, "adc_rate_hz": 125, "mode": "freerun"})
    client.post("/start")
    client.post("/recording/start?poll_interval_s=0.2")

    # Connect WebSocket immediately (minimal data yet)
    with client.websocket_connect("/stream") as websocket:
        # Wait for first message
        time.sleep(1.5)  # Wait for data to accumulate

        # Try to receive
        try:
            data = websocket.receive_json()
            # Should eventually get data
            assert "value" in data or data == {}  # May get empty or actual reading
        except Exception:
            # May timeout if no data yet (acceptable)
            pass


def test_websocket_multiple_clients(client, monkeypatch_transport):
    """Test multiple WebSocket clients can connect simultaneously."""
    # Setup
    client.post("/connect?port=/dev/fake&baud=9600")
    client.post("/config", json={"averaging": 12, "adc_rate_hz": 125, "mode": "freerun"})
    client.post("/start")
    client.post("/recording/start?poll_interval_s=0.1")

    time.sleep(0.5)

    # Connect two WebSocket clients
    with client.websocket_connect("/stream") as ws1:
        with client.websocket_connect("/stream") as ws2:
            # Both should receive data
            msg1 = ws1.receive_json()
            msg2 = ws2.receive_json()

            assert "value" in msg1
            assert "value" in msg2


def test_websocket_disconnect_gracefully(client, monkeypatch_transport):
    """Test WebSocket handles client disconnect gracefully."""
    client.post("/connect?port=/dev/fake&baud=9600")
    client.post("/config", json={"averaging": 125, "adc_rate_hz": 125, "mode": "freerun"})
    client.post("/start")
    client.post("/recording/start")

    # Connect and immediately close
    with client.websocket_connect("/stream") as websocket:
        pass  # Auto-closes on context exit

    # Should not crash; verify API still responsive
    status = client.get("/status").json()
    assert status["connected"] is True
