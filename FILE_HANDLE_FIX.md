# File Handle Race Condition Fix

## Issue Summary

The ChunkedDataStore was experiencing a **race condition during chunk rollover** that caused recording sessions to fail with:

```
ValueError: I/O operation on closed file
```

This error occurred at three locations:
- Line 618: `self._current_file.flush()` - trying to flush a closed file
- Line 655: `os.fsync(f.fileno())` - manifest fsync on closed file
- Line 553: `writer.writerow(row)` - CSV writer referencing closed file

## Root Cause

In the `_finalize_chunk()` method, the file handle was closed BEFORE the state variables were cleared:

```python
# OLD CODE (BUGGY):
def _finalize_chunk(self):
    # ... check if file exists ...

    # Close the file
    self._current_file.flush()
    os.fsync(self._current_file.fileno())
    self._current_file.close()

    # ... do SHA256 computation, manifest update ...
    # ... ~50 lines of work ...

    # FINALLY clear state (TOO LATE!)
    self._current_file = None
```

**The problem**: Between closing the file and setting `self._current_file = None`, if another thread called `_append_row()`, it would:

1. Check `if self._current_file is None:` → **FALSE** (not None yet, just closed)
2. Create CSV writer with closed file: `csv.DictWriter(self._current_file, ...)`
3. Try to write: `writer.writerow(row)` → **CRASH**

This race condition was guaranteed to occur during normal operation because:
- The DataRecorder polls and writes every 200ms
- Chunks roll over every 60 seconds (configurable)
- The gap between close and state clear was ~50-100ms
- High probability that a write attempt would occur during this window

## Fix Applied

**Solution**: Clear the state variables IMMEDIATELY after saving references, BEFORE closing the file.

```python
# NEW CODE (FIXED):
def _finalize_chunk(self):
    if self._current_file is None or self._current_path is None:
        return

    # Save references FIRST
    file_handle = self._current_file
    tmp_path = self._current_path
    chunk_rows = self._current_chunk_rows
    chunk_index = self._chunk_index

    # CRITICAL: Clear state IMMEDIATELY to prevent race condition
    self._current_file = None
    self._current_path = None
    self._chunk_start_time = None
    self._current_chunk_rows = 0
    self._current_chunk_bytes = 0

    # NOW safe to close (no thread can reference this handle anymore)
    file_handle.flush()
    os.fsync(file_handle.fileno())
    file_handle.close()

    # ... rest of finalization using saved references ...
```

## Why This Works

1. **Atomic state transition**: From the perspective of `_append_row()`, the state goes from "file open" to "file is None" instantaneously (under the RLock)
2. **No race window**: There's no window where `self._current_file` points to a closed file
3. **Safe references**: We save the actual file handle and metadata BEFORE clearing, so finalization can complete
4. **Next write blocked correctly**: If `_append_row()` is called during finalization, it sees `self._current_file is None` and opens a NEW chunk

## Testing

To verify the fix works:

```bash
# On Pi, run a recording for 70+ seconds (to trigger chunk rollover at 60s)
./test_qsensor_e2e.sh

# Expected behavior:
# - Recording starts successfully
# - At ~60 seconds, chunk rolls over (first chunk finalized)
# - Recording continues into second chunk
# - Stop succeeds with final chunk finalized
# - No "I/O operation on closed file" errors
```

## Files Changed

- `data_store/store.py`: Lines 608-680 - Complete rewrite of `_finalize_chunk()` method

## Deployment

```bash
# Load new image on Pi
gunzip -c q-sensor-api-pi-fix2.tar.gz | docker load

# Restart container
docker stop q-sensor
docker rm q-sensor
docker run -d \
  --name q-sensor \
  --privileged \
  -v /dev:/dev \
  -v /data/qsensor_recordings:/data/qsensor_recordings \
  -p 9150:9150 \
  -e SERIAL_PORT=/dev/ttyAMA0 \
  -e SERIAL_BAUD=115200 \
  q-sensor-api:pi
```

## Related Issues

This fix also addresses:
- Manifest fsync errors (line 655) - now using saved reference to open file
- Duplicate state clearing at end of function (removed)
- Improved thread safety by minimizing critical section duration
