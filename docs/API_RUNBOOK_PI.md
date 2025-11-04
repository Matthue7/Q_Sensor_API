# Q-Sensor API Hardware Validation Runbook (Raspberry Pi)

**Purpose:** Step-by-step hardware validation of the Q-Sensor API on Raspberry Pi with real sensor hardware.

**Prerequisites:**
- Raspberry Pi with Q-Sensor connected via USB (typically `/dev/ttyUSB0`)
- Q-Sensor API installed and dependencies installed (`pip install -e .`)
- `curl` (pre-installed on Raspberry Pi OS)
- Optional: `wscat` or `websocat` for WebSocket testing

---

## Part 1: Start the API Server

### 1.1 Navigate to Project Directory

```bash
cd ~/Q_Sensor_API
```

### 1.2 Start the API

```bash
./pi_runbooks/01_start_api.sh
```

You should see output like:

```
============================================
Starting Q-Sensor API...
============================================
Host: 0.0.0.0
Port: 8000
Serial Port: /dev/ttyUSB0
Serial Baud: 9600
...
INFO:     Application startup complete.
```

**Leave this terminal running.** Open a new SSH session or terminal for the next steps.

---

## Part 2: Basic Health Check

### 2.1 Test Health Endpoint

```bash
curl http://localhost:8000/
```

**Expected:**
```json
{
  "service": "Q-Sensor API",
  "version": "1.0.0",
  "status": "online"
}
```

### 2.2 Check OpenAPI Documentation

Open in browser (replace `<pi-ip>` with your Pi's IP):
```
http://<pi-ip>:8000/docs
```

You should see the interactive Swagger UI.

---

## Part 3: Connection & Configuration Flow

### 3.1 Connect to Sensor

```bash
curl -X POST "http://localhost:8000/sensor/connect?port=/dev/ttyUSB0&baud=9600"
```

**Expected:**
```json
{
  "status": "connected",
  "sensor_id": "QSR2150xxxxx"
}
```

**Note your sensor ID** for validation.

### 3.2 Check Status

```bash
curl http://localhost:8000/sensor/status
```

**Expected:**
```json
{
  "connected": true,
  "recording": false,
  "sensor_id": "QSR2150xxxxx",
  "mode": null,
  "rows": 0,
  "state": "config_menu"
}
```

### 3.3 Configure Sensor

Set averaging to 10 and ADC rate to 250 Hz:

```bash
curl -X POST http://localhost:8000/sensor/config \
  -H "Content-Type: application/json" \
  -d '{"averaging": 10, "adc_rate_hz": 250, "mode": "freerun"}'
```

**Expected:**
```json
{
  "averaging": 10,
  "adc_rate_hz": 250,
  "mode": "freerun",
  "tag": null,
  "sample_period_s": 0.04
}
```

**Validation:** Sample period should be `10 / 250 = 0.04` seconds (25 Hz).

---

## Part 4: Data Acquisition

### 4.1 Start Acquisition (Freerun Mode)

```bash
curl -X POST http://localhost:8000/sensor/start
```

**Expected:**
```json
{
  "status": "started",
  "mode": "freerun"
}
```

### 4.2 Get Latest Reading

Wait 1 second, then:

```bash
curl http://localhost:8000/sensor/latest
```

**Expected:**
```json
{
  "timestamp": "2025-11-04T12:34:56.789123+00:00",
  "sensor_id": "QSR2150xxxxx",
  "mode": "freerun",
  "value": 123.456,
  "TempC": 22.5,
  "Vin": 12.3
}
```

**Validation:**
- `timestamp` is ISO 8601 format
- `value` is a reasonable sensor reading (check expected range)
- `TempC` and `Vin` are present (may be `null` depending on firmware)

### 4.3 Get Recent Readings (Last 5 Seconds)

```bash
curl "http://localhost:8000/sensor/recent?seconds=5"
```

**Expected:**
```json
{
  "rows": [
    {
      "timestamp": "2025-11-04T12:34:56.789123+00:00",
      "sensor_id": "QSR2150xxxxx",
      "mode": "freerun",
      "value": 123.456,
      "TempC": 22.5,
      "Vin": 12.3
    },
    ...
  ]
}
```

**Validation:**
- `rows` array has multiple readings (expect ~125 readings for 5s at 25 Hz)
- Timestamps are sequential and recent

### 4.4 Get Statistics

```bash
curl http://localhost:8000/sensor/stats
```

**Expected:**
```json
{
  "row_count": 150,
  "start_time": "2025-11-04T12:34:50.123456+00:00",
  "end_time": "2025-11-04T12:35:00.123456+00:00",
  "duration_s": 10.0,
  "est_sample_rate_hz": 15.0
}
```

**Validation:**
- `est_sample_rate_hz` should be close to expected rate (~25 Hz for averaging=10, adc_rate=250)
- `duration_s` should match actual time since start

---

## Part 5: Pause & Resume

### 5.1 Pause Acquisition

```bash
curl -X POST http://localhost:8000/sensor/pause
```

**Expected:**
```json
{
  "status": "paused"
}
```

### 5.2 Verify Paused State

```bash
curl http://localhost:8000/sensor/status
```

**Expected:**
```json
{
  "connected": true,
  "recording": false,
  "sensor_id": "QSR2150xxxxx",
  "mode": null,
  "rows": 150,
  "state": "paused"
}
```

### 5.3 Resume Acquisition

```bash
curl -X POST http://localhost:8000/sensor/resume
```

**Expected:**
```json
{
  "status": "resumed",
  "mode": "freerun"
}
```

### 5.4 Verify Data Still Flowing

```bash
curl http://localhost:8000/sensor/latest
```

**Validation:** Timestamp should be recent (after resume).

---

## Part 6: Recording & Export

### 6.1 Start Recording

```bash
curl -X POST "http://localhost:8000/recording/start?poll_interval_s=0.2"
```

**Expected:**
```json
{
  "status": "recording"
}
```

### 6.2 Wait for Data

```bash
sleep 5
```

### 6.3 Check Status (Should Show Recording)

```bash
curl http://localhost:8000/sensor/status
```

**Expected:**
```json
{
  "connected": true,
  "recording": true,
  "sensor_id": "QSR2150xxxxx",
  "mode": "freerun",
  "rows": 200,
  "state": "acq_freerun"
}
```

### 6.4 Export CSV (Without Stopping Recorder)

```bash
curl -O -J http://localhost:8000/recording/export/csv
```

**Expected:**
- Downloads a file named `qsensor_data_YYYYMMDD_HHMMSS.csv`

**Validation:**

```bash
ls -lh qsensor_data_*.csv
head -20 qsensor_data_*.csv
```

**Check:**
- File exists and is non-empty
- Header row: `timestamp,sensor_id,mode,value,TempC,Vin`
- Data rows have ISO timestamps and numeric values

### 6.5 Export Parquet (Without Stopping Recorder)

```bash
curl -O -J http://localhost:8000/recording/export/parquet
```

**Expected:**
- Downloads `qsensor_data_YYYYMMDD_HHMMSS.parquet`

**Validation (requires pyarrow or pandas):**

```bash
python3 << 'EOF'
import pandas as pd
df = pd.read_parquet("qsensor_data_*.parquet")
print(df.head())
print(f"\nRows: {len(df)}")
print(f"Columns: {df.columns.tolist()}")
EOF
```

### 6.6 Stop Recording with CSV Flush

```bash
curl -X POST "http://localhost:8000/recording/stop?flush=csv"
```

**Expected:**
```json
{
  "status": "stopped",
  "flush_path": "/home/pi/Q_Sensor_API/qsensor_data_20251104_123456.csv"
}
```

**Validation:**

```bash
ls -lh qsensor_data_*.csv | tail -1
```

Check that the flushed CSV file exists at the path returned.

---

## Part 7: Stop Acquisition & Disconnect

### 7.1 Stop Acquisition

```bash
curl -X POST http://localhost:8000/sensor/stop
```

**Expected:**
```json
{
  "status": "stopped"
}
```

### 7.2 Verify State

```bash
curl http://localhost:8000/sensor/status
```

**Expected:**
```json
{
  "connected": true,
  "recording": false,
  "sensor_id": "QSR2150xxxxx",
  "mode": null,
  "rows": 200,
  "state": "config_menu"
}
```

### 7.3 Disconnect

```bash
curl -X POST http://localhost:8000/disconnect
```

**Expected:**
```json
{
  "status": "disconnected"
}
```

---

## Part 8: WebSocket Streaming (Optional)

### 8.1 Install wscat (if not installed)

```bash
sudo npm install -g wscat
```

**Alternative:** Use `websocat`:

```bash
sudo apt-get install websocat
```

### 8.2 Connect to WebSocket

**Using wscat:**

```bash
wscat -c ws://localhost:8000/sensor/stream
```

**Using websocat:**

```bash
websocat ws://localhost:8000/sensor/stream
```

### 8.3 Setup for Live Data

In another terminal, start acquisition:

```bash
curl -X POST "http://localhost:8000/sensor/connect?port=/dev/ttyUSB0&baud=9600"
curl -X POST http://localhost:8000/sensor/config -H "Content-Type: application/json" -d '{"mode": "freerun", "averaging": 1}'
curl -X POST http://localhost:8000/sensor/start
curl -X POST "http://localhost:8000/recording/start?poll_interval_s=0.1"
```

### 8.4 Observe WebSocket Output

You should see JSON messages every ~100ms:

```json
{"timestamp": "2025-11-04T12:34:56.789123+00:00", "sensor_id": "QSR2150xxxxx", "mode": "freerun", "value": 123.456, "TempC": 22.5, "Vin": 12.3}
{"timestamp": "2025-11-04T12:34:56.889123+00:00", "sensor_id": "QSR2150xxxxx", "mode": "freerun", "value": 123.789, "TempC": 22.5, "Vin": 12.3}
...
```

**Validation:**
- Messages arrive at ~10 Hz
- Timestamps update with each message
- Values change (sensor is reading live data)

**Exit:** Press Ctrl+C to disconnect.

### 8.5 Cleanup

```bash
curl -X POST http://localhost:8000/recording/stop
curl -X POST http://localhost:8000/sensor/stop
curl -X POST http://localhost:8000/disconnect
```

---

## Part 9: File Locations & Schema Validation

### 9.1 Check Export Directory

All CSV and Parquet files are saved in the working directory where the API was started:

```bash
ls -lh ~/Q_Sensor_API/qsensor_data_*.{csv,parquet}
```

### 9.2 Validate CSV Schema

```bash
head -1 qsensor_data_*.csv | head -1
```

**Expected Header:**
```
timestamp,sensor_id,mode,value,TempC,Vin
```

**Check a Data Row:**

```bash
sed -n '2p' qsensor_data_*.csv | head -1
```

**Expected Format:**
```
2025-11-04T12:34:56.789123+00:00,QSR2150xxxxx,freerun,123.456,22.5,12.3
```

### 9.3 Validate Parquet Schema

```bash
python3 << 'EOF'
import pandas as pd
import glob

parquet_files = glob.glob("qsensor_data_*.parquet")
if parquet_files:
    df = pd.read_parquet(parquet_files[0])
    print("Schema:")
    print(df.dtypes)
    print("\nFirst row:")
    print(df.iloc[0])
else:
    print("No Parquet files found")
EOF
```

**Expected Output:**
```
Schema:
timestamp           object
sensor_id           object
mode                object
value              float64
TempC              float64
Vin                float64
dtype: object

First row:
timestamp    2025-11-04T12:34:56.789123+00:00
sensor_id                      QSR2150xxxxx
mode                              freerun
value                               123.456
TempC                                  22.5
Vin                                    12.3
Name: 0, dtype: object
```

---

## Part 10: Shutdown Order Validation

**CRITICAL:** Verify that the API follows the correct shutdown order to prevent data loss.

### 10.1 Start Full Stack

```bash
curl -X POST "http://localhost:8000/sensor/connect?port=/dev/ttyUSB0&baud=9600"
curl -X POST http://localhost:8000/sensor/config -H "Content-Type: application/json" -d '{"mode": "freerun"}'
curl -X POST http://localhost:8000/sensor/start
curl -X POST "http://localhost:8000/recording/start?poll_interval_s=0.2"
sleep 2
```

### 10.2 Stop Acquisition (Should Auto-Stop Recorder First)

```bash
curl -X POST http://localhost:8000/sensor/stop
```

**Check API Logs (in server terminal):**

Look for:
```
WARNING - Recorder still running during /stop - stopping it first
INFO - Stopping acquisition...
```

**Validation:** ✅ Recorder stopped **before** acquisition stopped.

### 10.3 Test Disconnect with Active Recorder

```bash
curl -X POST "http://localhost:8000/sensor/connect?port=/dev/ttyUSB0&baud=9600"
curl -X POST http://localhost:8000/sensor/config -H "Content-Type: application/json" -d '{"mode": "freerun"}'
curl -X POST http://localhost:8000/sensor/start
curl -X POST "http://localhost:8000/recording/start?poll_interval_s=0.2"
sleep 2
curl -X POST http://localhost:8000/disconnect
```

**Check API Logs:**

Look for:
```
INFO - Stopping recorder before disconnect...
INFO - Stopping acquisition before disconnect...
INFO - Disconnecting from sensor...
```

**Validation:** ✅ Correct order: recorder → acquisition → disconnect.

---

## Part 11: Error Handling Tests

### 11.1 Test Connect Before Disconnect

```bash
curl -X POST "http://localhost:8000/sensor/connect?port=/dev/ttyUSB0&baud=9600"
curl -X POST "http://localhost:8000/sensor/connect?port=/dev/ttyUSB0&baud=9600"
```

**Expected (2nd call):**
```json
{
  "detail": "Already connected. Disconnect first."
}
```

**Status Code:** 400

### 11.2 Test Invalid Port

```bash
curl -X POST "http://localhost:8000/sensor/connect?port=/dev/nonexistent&baud=9600"
```

**Expected:**
```json
{
  "detail": "Port /dev/nonexistent not found or cannot be opened"
}
```

**Status Code:** 503

### 11.3 Test Recording Before Acquisition

```bash
curl -X POST "http://localhost:8000/sensor/connect?port=/dev/ttyUSB0&baud=9600"
curl -X POST "http://localhost:8000/recording/start"
```

**Expected:**
```json
{
  "detail": "Acquisition not running. Call /start first."
}
```

**Status Code:** 400

---

## Success Criteria Checklist

Use this checklist to validate the full hardware test:

- [ ] API starts successfully on `0.0.0.0:8000`
- [ ] Health check returns `200 OK`
- [ ] OpenAPI docs accessible at `/docs`
- [ ] Successfully connects to sensor via `/sensor/connect`
- [ ] Configuration updates work (`/sensor/config`)
- [ ] Acquisition starts and data flows (`/sensor/start`)
- [ ] Latest reading returns valid data (`/sensor/latest`)
- [ ] Recent readings return array of data (`/sensor/recent`)
- [ ] Statistics endpoint returns valid metrics (`/sensor/stats`)
- [ ] Pause/resume functionality works
- [ ] Recording starts successfully (`/recording/start`)
- [ ] CSV export works (both via `/export/csv` and `/recording/stop?flush=csv`)
- [ ] Parquet export works (both via `/export/parquet` and `/recording/stop?flush=parquet`)
- [ ] WebSocket streams live data at ~10 Hz
- [ ] Shutdown order is correct (recorder before controller)
- [ ] Error handling works for edge cases
- [ ] Exported files have correct schema and valid data

---

## Troubleshooting

### Issue: "Port /dev/ttyUSB0 not found"

**Solution:**
1. Check USB connection: `ls -l /dev/ttyUSB*`
2. Try `/dev/ttyACM0` or run `dmesg | tail` to see detected devices
3. Check permissions: `sudo usermod -a -G dialout $USER` (then logout/login)

### Issue: "Recorder not stopping cleanly"

**Solution:**
1. Always call `/recording/stop` before `/sensor/stop`
2. If stuck, restart the API server

### Issue: "WebSocket connects but no data"

**Solution:**
1. Ensure recorder is running (`/recording/start`)
2. Check that acquisition is active (`/sensor/status`)
3. Verify data is flowing (`/sensor/latest`)

### Issue: "Parquet export fails"

**Solution:**
1. Check that `pyarrow` is installed: `pip list | grep pyarrow`
2. Reinstall: `pip install pyarrow>=10.0.0`

---

## Notes for Production Deployment

1. **Systemd Service:** For automatic startup on boot, create a systemd service file.

2. **Firewall:** Open port 8000 if accessing from other machines:
   ```bash
   sudo ufw allow 8000/tcp
   ```

3. **HTTPS:** Consider using nginx reverse proxy with SSL for production.

4. **Monitoring:** Use the `/sensor/stats` endpoint to monitor sample rates and data quality.

5. **Logs:** API logs are written to stdout. Redirect to file if needed:
   ```bash
   ./pi_runbooks/01_start_api.sh > api.log 2>&1
   ```

---

**End of Runbook**
