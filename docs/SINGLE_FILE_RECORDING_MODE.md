# Single-File Recording Mode Design

## Overview

Currently, Q_Sensor_API uses a **chunked recording mode** where:
- Data is written to rolling CSV files (`chunk_00000.csv`, `chunk_00001.csv`, ...)
- Chunks are rolled every 60 seconds (configurable 15-300s)
- Each chunk is written atomically (.tmp → .csv rename)
- At the end of recording, the topside combines chunks into `session.csv`

**Problem**: The last in-progress chunk can be missed if the timing between `/record/stop` and the final mirroring poll is not correct.

**Proposed Solution**: Add a **single-file append-only mode** where:
- A single `session.csv` file grows throughout the recording
- Data is appended incrementally with periodic fsyncs for crash safety
- No post-recording combination step is needed
- Mirroring can fetch incremental updates (e.g., via HTTP Range requests or a "tail" endpoint)

## Design Requirements

### Crash Safety
- **CRITICAL**: If the Pi crashes mid-recording, the file must remain valid and readable up to the last fsync
- Solution: Use a write-ahead pattern:
  1. Append to `session.csv.tmp` in memory buffer
  2. Flush + fsync every N seconds (e.g., 10s)
  3. Only after fsync, update a `session.csv.offset` file with the valid byte count
  4. On crash recovery, truncate `session.csv.tmp` to the last valid offset

### Concurrent Read/Write
- **Challenge**: The Pi writes to the file while topside reads it via HTTP
- Solution:
  - Backend keeps an open file handle for append-only writes
  - Use OS-level file locking or a separate "reader-safe offset" marker
  - Expose a `/files/{session_id}/session.csv?offset=N` endpoint that serves bytes from offset N onwards

### Mirroring Efficiency
- **Challenge**: Avoid re-downloading the entire file on each poll
- Solution: Track the last mirrored byte offset on the topside
  - Poll: GET `/files/{session_id}/session.csv?offset=12345&limit=1MB`
  - Backend responds with new bytes since offset 12345
  - Topside appends to local `session.csv`

## Implementation Plan

### Phase 1: Backend Changes (Q_Sensor_API)

#### 1. New DataStore Class: `AppendOnlyDataStore`

**File**: `data_store/append_store.py`

```python
class AppendOnlyDataStore:
    """
    Crash-safe append-only CSV writer for single-file recording.

    Maintains:
    - session.csv.tmp: Active write file
    - session.csv.offset: Last fsynced byte offset (crash-safe marker)
    - manifest.json: Session metadata (rows, bytes, SHA256 of finalized file)

    Write pattern:
    1. Append rows to in-memory buffer
    2. Flush buffer to .tmp file every 10 seconds
    3. fsync() to ensure data is on disk
    4. Update .offset file atomically
    5. On /record/stop: rename .tmp → .csv, compute final SHA256
    """

    def __init__(self, session_id: str, base_path: Path, flush_interval_s: float = 10.0):
        self._session_id = session_id
        self._session_dir = Path(base_path) / session_id
        self._tmp_path = self._session_dir / "session.csv.tmp"
        self._final_path = self._session_dir / "session.csv"
        self._offset_path = self._session_dir / "session.csv.offset"
        self._manifest_path = self._session_dir / "manifest.json"

        self._file_handle: Optional[TextIOWrapper] = None
        self._buffer: List[dict] = []
        self._flush_interval = flush_interval_s
        self._last_flush_time = time.time()
        self._valid_offset = 0  # Crash-safe byte offset
        self._lock = RLock()

    def append_readings(self, readings: Iterable[Reading]) -> None:
        """Append readings to buffer, flush if interval elapsed."""
        with self._lock:
            rows = [reading_to_row(r) for r in readings]
            self._buffer.extend(rows)

            # Check if flush needed
            if time.time() - self._last_flush_time >= self._flush_interval:
                self._flush()

    def _flush(self) -> None:
        """Flush buffer to disk with fsync, update crash-safe offset."""
        if not self._buffer:
            return

        # Open file if not already open
        if self._file_handle is None:
            self._file_handle = open(self._tmp_path, 'a', encoding='utf-8', buffering=8192)

        # Write rows
        for row in self._buffer:
            line = ','.join(str(row[k]) for k in SCHEMA.keys()) + '\n'
            self._file_handle.write(line)

        # Force to disk
        self._file_handle.flush()
        os.fsync(self._file_handle.fileno())

        # Update crash-safe offset
        self._valid_offset = self._file_handle.tell()
        self._offset_path.write_text(str(self._valid_offset))

        # Clear buffer
        self._buffer.clear()
        self._last_flush_time = time.time()

    def finalize(self) -> SessionStats:
        """Finalize recording: flush, rename, compute SHA256."""
        with self._lock:
            # Final flush
            self._flush()

            # Close file handle
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None

            # Rename .tmp → .csv (atomic)
            self._tmp_path.rename(self._final_path)

            # Compute SHA256 of final file
            sha256 = hashlib.sha256()
            with open(self._final_path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    sha256.update(chunk)

            # Write manifest
            manifest = {
                "session_id": self._session_id,
                "file": "session.csv",
                "sha256": sha256.hexdigest(),
                "bytes": self._valid_offset,
                "rows": self._count_rows(),
                "finalized_at": datetime.now(timezone.utc).isoformat()
            }
            with open(self._manifest_path, 'w') as f:
                json.dump(manifest, f, indent=2)

            return SessionStats(...)  # Return final stats

    def get_tail(self, offset: int, limit: int = 1024 * 1024) -> bytes:
        """Read bytes from offset (for incremental mirroring)."""
        with open(self._tmp_path, 'rb') as f:
            f.seek(offset)
            return f.read(limit)
```

#### 2. New API Endpoint: `/files/{session_id}/tail`

**File**: `api/main.py`

```python
@app.get("/files/{session_id}/tail")
async def get_session_tail(
    session_id: str,
    offset: int = Query(0, description="Byte offset to read from"),
    limit: int = Query(1024 * 1024, description="Max bytes to read (default 1MB)")
):
    """
    Get incremental data from an append-only session file.

    Used by topside mirroring to fetch new bytes without re-downloading entire file.

    Args:
        session_id: Session UUID
        offset: Byte offset to start reading from
        limit: Maximum bytes to return (capped at 10MB)

    Returns:
        Binary response with new CSV data

    Headers:
        X-Current-Offset: Current valid byte offset (for next poll)
        X-Total-Rows: Total rows written so far
    """
    global _session_manager

    session = _session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    if not isinstance(session._store, AppendOnlyDataStore):
        raise HTTPException(status_code=400, detail="Session is not in append-only mode")

    # Cap limit at 10MB for safety
    limit = min(limit, 10 * 1024 * 1024)

    try:
        data = session._store.get_tail(offset, limit)
        current_offset = offset + len(data)

        return Response(
            content=data,
            media_type="text/csv",
            headers={
                "X-Current-Offset": str(current_offset),
                "X-Total-Rows": str(session._store.get_row_count()),
                "X-Session-Complete": "false" if session.is_recording else "true"
            }
        )
    except Exception as e:
        logger.error(f"Failed to read tail for session {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read data: {e}")
```

#### 3. Session Manager Update

**File**: `data_store/session_manager.py`

Add a `mode` parameter to `create_session()`:

```python
def create_session(
    self,
    mode: Literal["chunked", "append"] = "chunked",  # NEW
    mission: str,
    rate_hz: int,
    schema_version: int,
    roll_interval_s: float = 60.0,
    flush_interval_s: float = 10.0,  # For append mode
    target_chunk_mb: float = 2.0,
) -> RecordingSession:
    """
    Create recording session in either chunked or append mode.

    - chunked: Legacy mode (default) - rolling CSV chunks
    - append: New mode - single growing session.csv file
    """
    if mode == "append":
        store = AppendOnlyDataStore(
            session_id=session_id,
            base_path=self._base_path,
            flush_interval_s=flush_interval_s
        )
    else:
        store = ChunkedDataStore(
            session_id=session_id,
            base_path=self._base_path,
            roll_interval_s=roll_interval_s,
            target_chunk_mb=target_chunk_mb
        )

    # Rest of session creation...
```

### Phase 2: Topside Changes (Bio_cockpit)

#### 1. Update Mirroring Service

**File**: `src/electron/services/qsensor-mirror.ts`

Add append-only mode support:

```typescript
interface MirrorSession {
  mode: 'chunked' | 'append'  // NEW
  localOffset: number  // For append mode: track last downloaded byte
  // ... existing fields
}

/**
 * Poll for new data (append mode version)
 */
async function pollAndMirrorAppend(session: MirrorSession): Promise<void> {
  try {
    const url = `http://${session.vehicleAddress}:9150/files/${session.sessionId}/tail?offset=${session.localOffset}&limit=1048576`

    const response = await fetch(url, { signal: AbortSignal.timeout(15000) })
    if (!response.ok) {
      console.warn(`[QSensor Mirror] Tail request failed: HTTP ${response.status}`)
      return
    }

    // Read new bytes
    const newData = await response.arrayBuffer()
    const buffer = Buffer.from(newData)

    if (buffer.length === 0) {
      console.log(`[QSensor Mirror] No new data at offset ${session.localOffset}`)
      return
    }

    // Append to local session.csv
    const sessionPath = path.join(session.rootPath, 'session.csv')
    await fs.appendFile(sessionPath, buffer)

    // Update offset
    const newOffset = parseInt(response.headers.get('X-Current-Offset') || '0')
    session.localOffset = newOffset
    session.bytesMirrored += buffer.length
    session.lastSync = new Date().toISOString()

    console.log(`[QSensor Mirror] Appended ${buffer.length} bytes, new offset=${newOffset}`)

    // Update metadata
    await writeMirrorMetadata(session)
  } catch (error: any) {
    console.error(`[QSensor Mirror] Poll error (append mode):`, error.message)
  }
}
```

#### 2. Update UI to Select Mode

**File**: `src/views/ToolsQSeriesView.vue`

Add mode selection radio buttons:

```vue
<!-- Recording Mode Selection -->
<div class="flex items-center gap-4">
  <label class="text-sm font-medium min-w-[120px]">Recording mode:</label>
  <label class="flex items-center gap-2">
    <input type="radio" value="chunked" v-model="recordingMode" />
    <span class="text-sm">Chunked (legacy)</span>
  </label>
  <label class="flex items-center gap-2">
    <input type="radio" value="append" v-model="recordingMode" />
    <span class="text-sm">Single-file (append-only)</span>
  </label>
</div>
<div class="text-xs text-gray-400">
  <p><strong>Chunked:</strong> Data written to multiple CSV files, combined at end.</p>
  <p class="mt-1"><strong>Append-only:</strong> Single growing CSV file, no post-processing needed.</p>
</div>
```

### Phase 3: Testing & Validation

1. **Unit tests**:
   - Test `AppendOnlyDataStore.append_readings()` with 10k rows
   - Test crash recovery: kill process mid-write, verify file truncates to last valid offset
   - Test concurrent read/write: write thread + reader thread

2. **Integration tests**:
   - Start recording in append mode
   - Poll `/files/{session_id}/tail` every 5 seconds
   - Verify incremental data accumulates correctly on topside
   - Stop recording, verify final `session.csv` matches ROV's `session.csv`

3. **Stress tests**:
   - 1-hour recording at 15Hz (54,000 rows)
   - Simulate network interruptions (pause mirroring, resume)
   - Verify no data loss

## Migration Path

### Backward Compatibility

- **Default mode remains "chunked"** for existing users
- Add `mode` parameter to `/record/start` request body (optional, defaults to `"chunked"`)
- Frontend exposes mode selection in UI, but defaults to chunked

### Rollout Plan

1. **Phase 1**: Implement backend append mode as opt-in (v0.3.0)
2. **Phase 2**: Implement topside append mirroring (v0.3.0)
3. **Phase 3**: Test with alpha users, gather feedback (v0.3.1)
4. **Phase 4**: Make append mode the default, keep chunked as fallback (v0.4.0)
5. **Phase 5**: Deprecate chunked mode (v1.0.0, 6 months notice)

## Advantages of Append-Only Mode

### Pros
✅ **No missed data**: Final chunk is always included (no timing issues)
✅ **Simpler mirroring**: No chunk combining step, just incremental appends
✅ **Crash-safe**: Valid data recoverable up to last fsync
✅ **Better for long recordings**: Single file easier to manage than 100+ chunks
✅ **Real-time progress**: Topside can display live row counts during recording

### Cons
❌ **More complex implementation**: Requires careful offset tracking and fsync handling
❌ **Potential file corruption**: If offset file gets out of sync (mitigated by fsync ordering)
❌ **No chunk-level verification**: Can't verify SHA256 of individual chunks (only final file)
❌ **Slightly lower crash safety**: 10-second fsync interval means up to 10s of data at risk (vs 60s in chunked mode)

## Alternatives Considered

### Alternative 1: Keep Chunked Mode, Fix Timing

**Approach**: Keep current chunked architecture but fix the topside stop sequence:
1. Call `/record/stop` on backend
2. Wait for response (ensures last chunk finalized)
3. Poll `/record/snapshots` to get final chunk list
4. Download last chunk
5. Combine chunks

**Verdict**: ✅ **Recommended as short-term fix** (already implemented in this PR)

### Alternative 2: Backend Combines Chunks

**Approach**: Backend combines chunks into `session.csv` during `/record/stop`, then serves the combined file

**Pros**: Simpler topside code
**Cons**: Doubles storage (chunks + combined file), slow for large sessions
**Verdict**: ❌ Not recommended (storage waste)

### Alternative 3: Streaming CSV via WebSocket

**Approach**: Backend streams CSV rows in real-time via WebSocket, topside appends to local file

**Pros**: True real-time mirroring
**Cons**: Complex error recovery, difficult to resume after disconnect
**Verdict**: ❌ Over-engineered for this use case

## Conclusion

The **append-only single-file mode** is the right long-term solution for Q-Sensor recording:
- Eliminates the "missing last chunk" issue entirely
- Simplifies the mirroring architecture
- Maintains crash safety with proper fsync ordering
- Provides better user experience (one file, no combining step)

**Recommendation**:
1. ✅ Ship the **chunked mode timing fix** immediately (this PR)
2. ✅ Implement **append mode** as opt-in feature in v0.3.0
3. ✅ Gather user feedback and stabilize in v0.3.1
4. ✅ Make append mode the default in v0.4.0
