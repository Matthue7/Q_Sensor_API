# Q-Sensor API - System Architecture

## Overview

The Q-Sensor API is a self-contained BlueOS extension that provides real-time data acquisition and control for Biospherical Q-Series sensors. The system is designed as a modular, containerized application that integrates seamlessly with the BlueOS ecosystem on Raspberry Pi hardware.

**Design Principles**:
- **Modularity**: Clear separation between sensor control, data storage, and API layers
- **Thread Safety**: Concurrent access via locks and thread-safe data structures
- **Platform Independence**: Core library works standalone; API layer adds network access
- **Production Ready**: Comprehensive error handling, logging, and state management
- **Docker Native**: Designed for containerized deployment from the start

## System Components

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    BlueOS (Raspberry Pi 4)                       │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              Docker Container: q-sensor-api                 │ │
│  │                                                             │ │
│  │  ┌─────────────────────────────────────────────────────┐  │ │
│  │  │           FastAPI Application (api/main.py)          │  │ │
│  │  │                                                       │  │ │
│  │  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │ │
│  │  │  │   REST   │  │WebSocket │  │  Static Files    │  │  │ │
│  │  │  │Endpoints │  │Streaming │  │  (React webapp)  │  │  │ │
│  │  │  └────┬─────┘  └────┬─────┘  └──────────────────┘  │  │ │
│  │  │       │             │                                │  │ │
│  │  │       └─────────────┴────────────────┐              │  │ │
│  │  └────────────────────────────────────│──┴──────────────┘  │ │
│  │                                        │                    │ │
│  │  ┌─────────────────────────────────────▼──────────────┐   │ │
│  │  │          q_sensor_lib (Core Library)                │   │ │
│  │  │                                                      │   │ │
│  │  │  ┌──────────────────────────────────────────────┐  │   │ │
│  │  │  │        SensorController (controller.py)       │  │   │ │
│  │  │  │  - State machine (ConnectionState enum)       │  │   │ │
│  │  │  │  - Menu navigation                            │  │   │ │
│  │  │  │  - Configuration (averaging, ADC, mode)       │  │   │ │
│  │  │  │  - Acquisition start/stop/pause/resume        │  │   │ │
│  │  │  │  - Background reader thread (freerun/polled)  │  │   │ │
│  │  │  └────────┬───────────────────────┬───────────────┘  │   │ │
│  │  │           │                       │                   │   │ │
│  │  │  ┌────────▼────────┐    ┌────────▼────────┐         │   │ │
│  │  │  │   RingBuffer    │    │    Transport     │         │   │ │
│  │  │  │  (ring_buffer)  │    │   (transport)    │         │   │ │
│  │  │  │  - Thread-safe  │    │  - SerialLike    │         │   │ │
│  │  │  │  - 1000 items   │    │  - pyserial wrap │         │   │ │
│  │  │  └─────────────────┘    └─────────┬────────┘         │   │ │
│  │  │                                    │                   │   │ │
│  │  └────────────────────────────────────│───────────────────┘   │ │
│  │                                        │                       │ │
│  │  ┌─────────────────────────────────────▼───────────────┐     │ │
│  │  │          data_store (DataFrame Layer)                │     │ │
│  │  │                                                       │     │ │
│  │  │  ┌──────────────┐          ┌──────────────────┐     │     │ │
│  │  │  │  DataStore   │◀────────▶│  DataRecorder    │     │     │ │
│  │  │  │  (store.py)  │          │   (store.py)     │     │     │ │
│  │  │  │              │          │                  │     │     │ │
│  │  │  │ - DataFrame  │          │ - Background     │     │     │ │
│  │  │  │ - Thread-safe│          │   polling thread │     │     │ │
│  │  │  │ - CSV export │          │ - Polls          │     │     │ │
│  │  │  │ - Parquet    │          │   RingBuffer     │     │     │ │
│  │  │  └──────────────┘          └──────────────────┘     │     │ │
│  │  └───────────────────────────────────────────────────────┘     │ │
│  │                                        │                       │ │
│  └────────────────────────────────────────│───────────────────────┘ │
│                                            │                         │
│  ┌─────────────────────────────────────────▼───────────────────┐   │
│  │                     /dev/ttyUSB0                             │   │
│  │                  (mapped into container)                     │   │
│  └──────────────────────────────┬───────────────────────────────┘   │
└─────────────────────────────────│───────────────────────────────────┘
                                  │
                     ┌────────────▼───────────┐
                     │   Q-Series Sensor      │
                     │  (firmware 4.003)      │
                     │  - Freerun streaming   │
                     │  - Polled queries      │
                     │  - Menu configuration  │
                     └────────────────────────┘
```

### Component Breakdown

#### 1. FastAPI Layer (`api/`)

**Purpose**: Provides HTTP REST and WebSocket interfaces to the core library

**Location**: `api/main.py` (888 lines)

**Responsibilities**:
- **REST Endpoints**: CRUD operations for sensor control
- **WebSocket Streaming**: Real-time data broadcast
- **Static File Serving**: React webapp and simple UI
- **CORS Management**: Cross-origin access for web UIs
- **Service Discovery**: BlueOS `/register_service` endpoint
- **Error Translation**: Maps library exceptions to HTTP status codes

**Key Design Decisions**:
- **Singleton Pattern**: One global controller/store/recorder instance
- **Thread-Safe Access**: All state changes protected by `RLock`
- **Auto-start Recording**: `/sensor/start` automatically starts DataRecorder
- **Graceful Degradation**: Stops recorder before stopping acquisition

**State Management**:
```python
# Global state (module-level)
_controller: Optional[SensorController] = None
_store: Optional[DataStore] = None
_recorder: Optional[DataRecorder] = None
_lock = RLock()  # Protects state-changing operations
```

**Endpoints**:
- **Sensor**: `/sensor/connect`, `/sensor/start`, `/sensor/stop`, etc.
- **Recording**: `/recording/start`, `/recording/export/csv`, etc.
- **Service**: `/`, `/health`, `/register_service`, `/docs`
- **Streaming**: `WS /stream` (WebSocket)

#### 2. Core Sensor Library (`q_sensor_lib/`)

**Purpose**: Hardware abstraction and protocol implementation

**Location**: `q_sensor_lib/` (1,848 LOC across 8 files)

**Responsibilities**:
- **Connection Management**: Open/close serial port
- **Protocol Implementation**: Menu navigation, command parsing
- **State Machine**: Track connection state (DISCONNECTED → CONNECTED → ACQ_FREERUN/ACQ_POLLED → PAUSED)
- **Data Acquisition**: Background thread polls sensor (freerun or polled mode)
- **Buffering**: Thread-safe ring buffer for readings
- **Configuration**: Get/set averaging, ADC rate, mode, TAG

**File Structure**:
```
q_sensor_lib/
├── __init__.py         # Public API (exports SensorController, models, errors)
├── controller.py       # Main SensorController class (918 lines)
├── transport.py        # Serial port wrapper (211 lines)
├── protocol.py         # Wire protocol constants (241 lines)
├── parsing.py          # Response parsing functions (222 lines)
├── models.py           # Data classes (SensorConfig, Reading) (114 lines)
├── errors.py           # Custom exceptions (37 lines)
└── ring_buffer.py      # Thread-safe circular buffer (76 lines)
```

**Key Classes**:

**SensorController**:
- **State Machine**: ConnectionState enum (DISCONNECTED, CONFIG_MENU, ACQ_FREERUN, ACQ_POLLED, PAUSED, ERROR)
- **Background Thread**: `_reader_thread` continuously reads serial port
- **Thread Safety**: Lock protects state transitions, buffer access
- **Lifecycle**: connect() → set_mode() → start_acquisition() → read_buffer_snapshot() → stop() → disconnect()

**Transport**:
- **Protocol**: `SerialLike` (allows FakeSerial substitution)
- **Wrapper**: `Transport` class wraps pyserial with logging
- **Encoding**: ASCII, CR termination
- **Timeouts**: Configurable read/write timeouts

**RingBuffer**:
- **Capacity**: 1000 items (configurable)
- **Thread-Safe**: `threading.Lock` protects all operations
- **Methods**: `append()`, `get_snapshot()`, `clear()`

#### 3. DataFrame Recording Layer (`data_store/`)

**Purpose**: Persistent data storage using pandas

**Location**: `data_store/` (570 LOC across 3 files)

**Responsibilities**:
- **DataFrame Storage**: pandas DataFrame with schema
- **Background Recording**: DataRecorder thread polls controller buffer
- **Export**: CSV and Parquet formats
- **Statistics**: Row count, time range, sample rate estimation

**File Structure**:
```
data_store/
├── __init__.py         # Public API (exports DataStore, DataRecorder)
├── store.py            # DataStore and DataRecorder classes (507 lines)
└── schemas.py          # DataFrame schema and conversion (62 lines)
```

**Key Classes**:

**DataStore**:
- **Storage**: pandas DataFrame with predefined schema
- **Thread Safety**: `RLock` protects DataFrame access
- **Methods**: `append()`, `get_dataframe()`, `export_csv()`, `export_parquet()`, `get_stats()`
- **Auto-Flush**: Optional periodic flush to disk

**DataRecorder**:
- **Background Thread**: Polls controller buffer every `poll_interval_s` (default 0.2s)
- **Lifecycle**: `start()` → running → `stop()`
- **Integration**: Reads from `SensorController.read_buffer_snapshot()`
- **Flush on Stop**: Optional export when stopping

**Schema**:
```python
SCHEMA = {
    'timestamp': 'datetime64[ns]',
    'value': 'float64',
    'tag': 'object',      # Optional (polled mode)
    'temp_c': 'float64',  # Optional
    'vin': 'float64'      # Optional
}
```

#### 4. Web UI (`api/static/`)

**Purpose**: Browser-based user interface

**Location**: `api/static/webapp/` (React build) and `api/static/blueos-ui/` (simple UI)

**Responsibilities**:
- **Sensor Control**: Connect, configure, start/stop acquisition
- **Data Visualization**: Real-time chart, latest reading display
- **Export**: Download CSV/Parquet files
- **Status Display**: Connection state, sample rate, row count

**Architecture**:

**React Webapp** (`webapp/`):
- **Framework**: React 18 + TypeScript
- **Build Tool**: Vite
- **Styling**: TailwindCSS
- **State**: Zustand store
- **Charts**: Recharts
- **Size**: ~150KB gzipped

**Simple UI** (`blueos-ui/`):
- **Framework**: Vanilla JavaScript
- **Charts**: Chart.js
- **Size**: ~13KB total
- **Purpose**: Lightweight fallback

**API Communication**:
- **REST**: Fetch API for CRUD operations
- **WebSocket**: Real-time data stream (optional)
- **Polling**: Falls back to `/sensor/latest` polling if WebSocket unavailable
- **CORS**: Configured for `blueos.local` origin

#### 5. Test Infrastructure (`fakes/`, `tests/`)

**Purpose**: Hardware-free testing and validation

**Location**: `fakes/fake_serial.py` (729 lines), `tests/` (2,729 LOC)

**FakeSerial** (Device Simulator):
- **Protocol**: Mimics firmware 4.003 behavior
- **Modes**: Freerun and polled
- **Menu**: Complete menu navigation simulation
- **Data**: Generates realistic sensor readings
- **Usage**: `controller.connect(port="FAKE")`

**Test Suite**:
- **Framework**: pytest
- **Coverage**: Unit tests for all major components
- **No Hardware**: Uses FakeSerial for all tests
- **Fast**: All tests complete in < 30 seconds

**Test Categories**:
1. **Connection**: `test_connect_and_menu.py`
2. **Configuration**: `test_config_set_get.py`
3. **Freerun**: `test_freerun_stream.py`
4. **Polled**: `test_polled_sequence.py`
5. **Lifecycle**: `test_pause_resume_stop.py`
6. **Errors**: `test_error_paths.py`
7. **DataFrame**: `test_data_recorder_basic.py`
8. **API**: `test_api_endpoints.py`
9. **WebSocket**: `test_api_ws.py`

## Data Flow

### End-to-End Data Flow

```
┌─────────┐
│  User   │ Clicks "Start" in React webapp
└────┬────┘
     │ HTTP POST /sensor/start
     ▼
┌─────────────────┐
│  FastAPI Layer  │
│  (api/main.py)  │
│                 │
│  start_acquisition():
│    1. controller.start_acquisition(poll_hz=1.0)
│    2. Create DataRecorder instance
│    3. recorder.start()
│    4. Return {"status": "started", "recording": true}
└────┬────────────┘
     │
     ▼
┌─────────────────────────────────────┐
│  SensorController                    │
│  (q_sensor_lib/controller.py)        │
│                                      │
│  start_acquisition():
│    1. Exit menu → send command
│    2. Enter ACQ_FREERUN state
│    3. Start _reader_thread
│                                      │
│  _reader_thread (background):        │
│    while acquiring:                  │
│      1. Read line from serial        │
│      2. Parse reading                │
│      3. Append to RingBuffer         │
│      4. Sleep briefly                │
└────┬────────────────────────────────┘
     │
     │ Serial I/O
     ▼
┌──────────────────┐
│  Transport       │
│  (pyserial wrap) │
│                  │
│  read_line():
│    serial.readline()
└────┬─────────────┘
     │
     │ /dev/ttyUSB0 (Docker mapped)
     ▼
┌──────────────────┐
│  Q-Series Sensor │
│  (firmware 4.003)│
│                  │
│  Streaming data:
│    "1.234567\r\n"
│    "1.234568\r\n"
│    ...
└──────────────────┘

     ▲ Readings flow back up
     │
┌────▼────────────────────────────────┐
│  RingBuffer (q_sensor_lib)           │
│  - Thread-safe circular buffer       │
│  - Capacity: 1000 items              │
│  - Latest readings always available  │
└────┬─────────────────────────────────┘
     │
     │ Polled by DataRecorder
     ▼
┌─────────────────────────────────────┐
│  DataRecorder (data_store/store.py) │
│  - Background thread                 │
│  - Polls every 0.2s                  │
│                                      │
│  run_loop():
│    while running:                    │
│      1. readings = controller.read_buffer_snapshot()
│      2. store.append(readings)
│      3. Sleep(poll_interval_s)
└────┬─────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────┐
│  DataStore (pandas DataFrame)        │
│  - Thread-safe DataFrame access      │
│  - Schema: timestamp, value, ...     │
│  - Export: CSV, Parquet              │
└────┬─────────────────────────────────┘
     │
     │ Frontend polls /sensor/latest
     ▼
┌─────────────────────────────────────┐
│  React Webapp                        │
│  - Polls /sensor/latest every 500ms  │
│  - Updates live chart                │
│  - Displays current value            │
│  - Shows sample rate, row count      │
└──────────────────────────────────────┘
```

### WebSocket Streaming (Alternative)

```
React Webapp                       FastAPI Layer
     │                                  │
     │  WS connect /stream              │
     ├──────────────────────────────────▶│
     │                                  │ WebSocket connection accepted
     │                                  │
     │                                  │ Start streaming loop:
     │                                  │   while connected:
     │                                  │     reading = controller.read_buffer_snapshot()[-1]
     │◀─────────────────────────────────┤     ws.send_json(reading)
     │  {"value": 1.234, "ts": "..."}   │     await asyncio.sleep(0.1)
     │                                  │
     │◀─────────────────────────────────┤
     │  {"value": 1.235, "ts": "..."}   │
     │                                  │
     │  ...                             │
```

## BlueOS Integration

### Service Discovery

BlueOS uses a "Helper" service that periodically scans for services:

```
BlueOS Helper (every 5-10 seconds)
     │
     │ HTTP GET http://localhost:9150/register_service
     ▼
Q-Sensor API
     │
     │ Returns JSON metadata:
     ▼
{
  "name": "Q-Sensor API",
  "icon": "mdi-chart-line",
  "webpage": "/",
  "api": "/docs",
  "version": "1.0.0",
  ...
}
     │
     ▼
BlueOS Helper
     │
     │ Adds to sidebar with icon
     ▼
BlueOS Web UI
     │
     │ User clicks "Q-Sensor API"
     ▼
Opens: http://blueos.local/extension/qsensorapi/
     │
     │ BlueOS NGINX proxies to:
     ▼
http://localhost:9150/ (FastAPI serves React webapp)
```

### Docker Integration

**Network Mode**: `--network host`
- Container shares Pi's network namespace
- API accessible at `blueos.local:9150`
- No port mapping needed

**Device Mapping**: `--device /dev/ttyUSB0:/dev/ttyUSB0`
- USB serial device mapped into container
- `--group-add dialout` for permissions

**Volume Mount**: `-v /usr/blueos/userdata/q_sensor:/data`
- Persistent storage for exported data
- Survives container restarts

**Environment Variables**:
- `SERIAL_PORT=/dev/ttyUSB0`
- `SERIAL_BAUD=9600`
- `LOG_LEVEL=INFO`

**Restart Policy**: `--restart unless-stopped`
- Auto-restart on crash
- Survives Pi reboot

### BlueOS Proxy Routing

When user clicks sidebar:
```
http://blueos.local/extension/qsensorapi/?full_page=true
                     │
                     │ BlueOS NGINX config:
                     │   location /extension/qsensorapi/ {
                     │     proxy_pass http://localhost:9150/;
                     │   }
                     ▼
http://localhost:9150/
                     │
                     ▼
FastAPI serves: /app/api/static/webapp/index.html
                     │
                     ▼
React app loads
     │
     │ Makes API calls (relative URLs):
     │   fetch("/sensor/status")
     ▼
http://blueos.local/extension/qsensorapi/sensor/status
                     │ (proxied to localhost:9150/sensor/status)
                     ▼
FastAPI endpoint handler
```

## Thread Architecture

### Thread Overview

The system uses multiple threads for concurrent operation:

```
┌──────────────────────────────────────────────────────────────┐
│  Main Thread (uvicorn)                                        │
│  - FastAPI request handling                                   │
│  - WebSocket connections                                      │
│  - API endpoint execution                                     │
└────┬─────────────────────────────────────────────────────────┘
     │ Creates/manages:
     │
     ├─▶ ┌─────────────────────────────────────────────────────┐
     │   │  SensorController._reader_thread                     │
     │   │  - Started by start_acquisition()                    │
     │   │  - Continuously reads serial port                    │
     │   │  - Parses readings                                   │
     │   │  - Appends to RingBuffer                             │
     │   │  - Stopped by stop() or pause()                      │
     │   └──────────────────────────────────────────────────────┘
     │
     └─▶ ┌─────────────────────────────────────────────────────┐
         │  DataRecorder._thread                                │
         │  - Started by recorder.start()                       │
         │  - Polls controller buffer every 0.2s                │
         │  - Appends readings to DataFrame                     │
         │  - Stopped by recorder.stop()                        │
         └──────────────────────────────────────────────────────┘
```

### Thread Synchronization

**SensorController**:
- `self._buffer_lock` protects RingBuffer access
- `self._state_lock` protects state transitions
- Background thread only writes to buffer
- Main thread reads buffer snapshots

**DataStore**:
- `self._lock` (RLock) protects DataFrame access
- DataRecorder thread appends rows
- API thread reads for export/stats

**Critical Sections**:
```python
# SensorController
with self._buffer_lock:
    self._buffer.append(reading)

# DataStore
with self._lock:
    self._df = pd.concat([self._df, new_rows])
```

## State Management

### SensorController State Machine

```
┌──────────────┐
│ DISCONNECTED │ Initial state
└──────┬───────┘
       │ connect()
       ▼
┌──────────────┐
│ CONFIG_MENU  │ Connected, in menu
└──────┬───────┘
       │ start_acquisition()
       │
       ├─────────────────────────────┐
       │                             │
       ▼                             ▼
┌──────────────┐             ┌──────────────┐
│ ACQ_FREERUN  │             │ ACQ_POLLED   │
│              │             │              │
│ - Streaming  │             │ - Polling    │
│ - Reader on  │             │ - Reader on  │
└──────┬───────┘             └──────┬───────┘
       │                             │
       │ pause()                     │ pause()
       ▼                             ▼
┌──────────────┐             ┌──────────────┐
│    PAUSED    │◀────────────┤    PAUSED    │
│              │             │              │
│ - In menu    │             │ - In menu    │
│ - Can resume │             │ - Can resume │
└──────┬───────┘             └──────┬───────┘
       │                             │
       │ resume()                    │ resume()
       ▼                             ▼
┌──────────────┐             ┌──────────────┐
│ ACQ_FREERUN  │             │ ACQ_POLLED   │
└──────┬───────┘             └──────┬───────┘
       │                             │
       │ stop()                      │ stop()
       ▼                             ▼
┌──────────────┐
│ CONFIG_MENU  │
└──────┬───────┘
       │ disconnect()
       ▼
┌──────────────┐
│ DISCONNECTED │
└──────────────┘

       │ (any error)
       ▼
┌──────────────┐
│    ERROR     │ Terminal state, must disconnect
└──────────────┘
```

**State Transitions**:
- `connect()`: DISCONNECTED → CONFIG_MENU
- `start_acquisition()`: CONFIG_MENU → ACQ_FREERUN or ACQ_POLLED
- `pause()`: ACQ_* → PAUSED
- `resume()`: PAUSED → ACQ_* (same mode)
- `stop()`: ACQ_* or PAUSED → CONFIG_MENU
- `disconnect()`: * → DISCONNECTED
- Error: * → ERROR

### DataRecorder State

```
┌─────────┐
│ STOPPED │ Initial state
└────┬────┘
     │ start()
     ▼
┌─────────┐
│ RUNNING │ Background thread polling
└────┬────┘
     │ stop()
     ▼
┌─────────┐
│ STOPPED │ Thread joined, optional flush
└─────────┘
```

## Error Handling

### Exception Hierarchy

```
Exception
└─ q_sensor_lib.errors.*
   ├─ SensorError (base class)
   │  ├─ MenuTimeout           # Menu command didn't complete in time
   │  ├─ InvalidConfigValue    # Config value out of range
   │  ├─ SerialIOError         # Serial port communication failure
   │  ├─ UnexpectedResponse    # Sensor returned unexpected data
   │  └─ ConnectionLost        # Serial connection dropped
   └─ DataStoreError (base class)
      ├─ BufferOverflow        # DataFrame exceeded max_rows
      └─ ExportError           # File export failed
```

### API Error Mapping

FastAPI translates library exceptions to HTTP status codes:

| Exception | HTTP Status | Example |
|-----------|-------------|---------|
| `MenuTimeout` | 504 Gateway Timeout | Menu command took > 5s |
| `InvalidConfigValue` | 400 Bad Request | averaging=-1 (invalid) |
| `SerialIOError` | 503 Service Unavailable | Serial port disconnected |
| `UnexpectedResponse` | 500 Internal Server Error | Sensor sent malformed data |
| `ConnectionLost` | 503 Service Unavailable | USB cable unplugged |
| Generic `Exception` | 500 Internal Server Error | Unhandled error |

**Example**:
```python
try:
    controller.set_averaging(averaging)
except MenuTimeout:
    raise HTTPException(status_code=504, detail="Menu timeout during config")
except InvalidConfigValue as e:
    raise HTTPException(status_code=400, detail=str(e))
except SerialIOError:
    raise HTTPException(status_code=503, detail="Serial communication error")
```

## Platform-Specific Considerations

### ARMv7 (Raspberry Pi 4)

**Challenge**: pandas 2.x doesn't have ARMv7 wheels, requires compilation from source

**Solution**:
- Pin pandas==1.3.5 (last version with ARMv7 wheels)
- Use full `python:3.10` image (not slim) for build tools
- Cross-compile on Mac/Linux using `docker buildx --platform linux/arm/v7`

**Build Time**:
- Mac cross-compile: ~100 minutes (one-time)
- Pi native build: ~100 minutes (avoid if possible)

**Dependencies**:
- Python 3.10 (not 3.11 - pandas 1.3.5 incompatibility)
- numpy 1.21.6 (pandas dependency, no separate install)
- pyarrow for Parquet export (optional)

### Serial vs. Ethernet

**Serial (USB)**:
- **Default**: `/dev/ttyUSB0` at 9600 baud
- **Container**: Requires `--device` mapping
- **Permissions**: Requires `--group-add dialout`
- **Detection**: `/dev/ttyUSB*` appears when USB adapter plugged in
- **Use Case**: Development, testing, standalone deployments

**Ethernet** (Future):
- **Protocol**: Same ASCII protocol over TCP socket
- **Container**: No special device mapping needed
- **Scalability**: Multiple sensors on network
- **Use Case**: Production deployments, multi-sensor arrays

### Docker Networking

**Host Mode** (`--network host`):
- **Pros**: Simple, no port mapping, BlueOS integration easy
- **Cons**: Less isolation, shares Pi's network namespace
- **Alternative**: Bridge mode with port mapping (`-p 9150:9150`)

## Security Considerations

### Access Control

- **No Authentication**: API is open (trust network boundary)
- **CORS**: Restricts browser access to configured origins
- **BlueOS Integration**: Relies on BlueOS for user auth

### Serial Port Access

- **Device Mapping**: Only specific device (`/dev/ttyUSB0`) mapped
- **Group Membership**: `dialout` group required for serial access
- **No Privileged Mode**: Container doesn't run as root unnecessarily

### Data Persistence

- **Volume Mount**: Data persists in `/usr/blueos/userdata/q_sensor/`
- **No Secrets**: No credentials or API keys stored
- **Export Files**: CSV/Parquet files only (no executable code)

## Performance Characteristics

### Throughput

**Freerun Mode**:
- Sample rate: 1 Hz typical (ADC 125 Hz ÷ averaging 125)
- Max sample rate: ~10 Hz (ADC 125 Hz ÷ averaging 12)
- Latency: < 100ms from sensor → frontend

**Polled Mode**:
- Poll rate: User-configurable (default 1 Hz)
- Max poll rate: ~5 Hz (limited by menu command overhead)
- Latency: ~200ms (command + response time)

### Memory Usage

**Container**:
- Base image: ~200MB
- Python dependencies: ~100MB
- Runtime: ~50MB (varies with DataFrame size)
- Total: ~350MB

**DataFrame**:
- Default max: 100,000 rows
- Memory per row: ~150 bytes
- Max memory: ~15MB for DataFrame
- Auto-flush: Optional to disk before limit

### CPU Usage

**Idle**: < 1% CPU
**Acquiring (1 Hz)**: < 5% CPU
**Acquiring (10 Hz)**: < 15% CPU
**WebSocket streaming**: +2-5% CPU per client

## Deployment Scenarios

### Development (Mac/Linux)

```bash
# 1. Local Python environment
pip install -e ".[dev]"
python examples/freerun_demo.py --fake

# 2. Local API server
uvicorn api.main:app --reload --port 9150
# Connect with port="FAKE" in Swagger UI

# 3. Local Docker
docker build -t q-sensor-api:dev .
docker run -p 9150:9150 q-sensor-api:dev
```

### Production (Raspberry Pi + BlueOS)

```bash
# Cross-compile on Mac/Linux
docker buildx build --platform linux/arm/v7 -t q-sensor-api:pi --load .

# Transfer to Pi
docker save q-sensor-api:pi -o ~/q-sensor-api-pi.tar
scp ~/q-sensor-api-pi.tar pi@blueos.local:/tmp/

# Deploy on Pi
ssh pi@blueos.local << 'EOF'
  docker load -i /tmp/q-sensor-api-pi.tar
  docker run -d --name q-sensor \
    --network host \
    --device /dev/ttyUSB0:/dev/ttyUSB0 \
    --group-add dialout \
    -v /usr/blueos/userdata/q_sensor:/data \
    -e SERIAL_PORT=/dev/ttyUSB0 \
    --restart unless-stopped \
    q-sensor-api:pi
EOF
```

### Testing (CI/CD)

```bash
# All tests use FakeSerial - no hardware needed
pip install -e ".[dev]"
pytest --cov=q_sensor_lib --cov=api --cov=data_store
pytest --timeout=10  # Prevent hanging tests
```

## Future Enhancements

### Potential Improvements

1. **Multi-Sensor Support**
   - Connect to multiple sensors simultaneously
   - Aggregate data from sensor array
   - Requires refactoring singleton pattern

2. **Ethernet Transport**
   - TCP socket support (same protocol)
   - No USB device mapping needed
   - Better for production deployments

3. **Authentication**
   - API key or token-based auth
   - Integration with BlueOS user management
   - HTTPS support

4. **Advanced Data Processing**
   - Real-time filtering (moving average, outlier detection)
   - Calibration curve application
   - Units conversion

5. **Database Storage**
   - TimescaleDB or InfluxDB for time-series
   - Better query performance for large datasets
   - Historical data retention policies

6. **Monitoring & Alerting**
   - Prometheus metrics export
   - Threshold-based alerts
   - Health check dashboard

7. **Configuration Persistence**
   - Save/load sensor configurations
   - User presets
   - Auto-reconnect on startup

## References

### External Documentation

- **BlueOS**: https://github.com/bluerobotics/BlueOS
- **FastAPI**: https://fastapi.tiangolo.com/
- **pandas**: https://pandas.pydata.org/
- **Docker**: https://docs.docker.com/
- **pytest**: https://docs.pytest.org/

### Internal Documentation

- **README.md**: Quick start and overview
- **API_REFERENCE.md**: Complete API endpoint documentation
- **WEBAPP_BUILD.md**: Web UI build process
- **DATAFRAME_RECORDING.md**: DataFrame layer usage
- **00_Serial_protocol_2150.md**: Low-level protocol specification
- **CHANGELOG.md**: Version history

---

**Document Version**: 1.0.0
**Last Updated**: 2025-11-10
**Author**: Biospherical Instruments Inc.
