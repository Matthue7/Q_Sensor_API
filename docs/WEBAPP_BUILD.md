# Web UI Build Process

The Q-Sensor API includes two web interfaces served as static files:

## 1. BlueOS Simple UI (`api/static/blueos-ui/`)

A lightweight Chart.js-based interface with:
- Real-time sensor data display
- Live chart with 60-second window
- Connect/Start/Stop/Disconnect controls
- WebSocket streaming support
- BlueOS dark theme styling

**Files:**
- `index.html` - Main HTML structure
- `style.css` - BlueOS-themed styles (4.3KB)
- `app.js` - JavaScript with Chart.js integration (8.7KB)

**Usage:** Accessed via BlueOS extension sidebar

## 2. React Production Webapp (`api/static/webapp/`)

A full-featured React 18 + TypeScript application built from a separate repository.

### Source Repository

**Location:** `/Users/matthuewalsh/qseries-noise/Q_Web`

**Tech Stack:**
- React 18
- TypeScript
- Vite (build tool)
- TailwindCSS (styling)
- Recharts (data visualization)
- Zustand (state management)

### Build Process

```bash
# Navigate to React app source
cd /Users/matthuewalsh/qseries-noise/Q_Web

# Install dependencies (first time only)
npm install

# Build production bundle
npm run build

# Output directory: dist/
# - dist/index.html (entry point)
# - dist/assets/*.js (bundled JavaScript)
# - dist/assets/*.css (bundled styles)
```

### Deploy to API Server

```bash
# Copy built files to API static directory
cp -r dist/* ../Q_Sensor_API/api/static/webapp/

# Verify deployment
ls -lh ../Q_Sensor_API/api/static/webapp/
# Should see:
# - index.html (469B)
# - assets/index-*.js (bundled JS)
# - assets/index-*.css (bundled CSS)
```

### Rebuild Docker Image

After updating the webapp:

```bash
cd /Users/matthuewalsh/qseries-noise/Q_Sensor_API

# Rebuild Docker image
docker buildx build --platform linux/arm/v7 -t q-sensor-api:pi --load .

# Save and transfer to Pi
docker save q-sensor-api:pi -o ~/q-sensor-api-pi.tar
scp ~/q-sensor-api-pi.tar pi@blueos.local:/tmp/

# Restart container on Pi
ssh pi@blueos.local << 'EOF'
  docker load -i /tmp/q-sensor-api-pi.tar
  docker stop q-sensor && docker rm q-sensor
  docker run -d --name q-sensor \
    --network host \
    --device /dev/ttyUSB0:/dev/ttyUSB0 \
    --group-add dialout \
    -v /usr/blueos/userdata/q_sensor:/data \
    -e SERIAL_PORT=/dev/ttyUSB0 \
    -e SERIAL_BAUD=9600 \
    -e LOG_LEVEL=INFO \
    --restart unless-stopped \
    q-sensor-api:pi
EOF
```

## API Integration

Both UIs communicate with the FastAPI backend using:

### REST Endpoints
- `GET /sensor/status` - Connection and acquisition status
- `POST /sensor/connect` - Connect to sensor
- `POST /sensor/start` - Start data acquisition (auto-starts DataRecorder)
- `POST /sensor/stop` - Stop acquisition
- `POST /sensor/disconnect` - Disconnect from sensor
- `GET /sensor/latest` - Get most recent reading
- `GET /sensor/recent?seconds=60` - Get recent readings
- `GET /sensor/stats` - Get DataFrame statistics

### WebSocket Streaming
- `WS /stream` - Real-time data stream during acquisition

### Configuration
The React app uses `API_BASE = ''` (empty string) for relative URLs, which:
- Works correctly through BlueOS NGINX proxy
- Routes all API calls through the same origin
- Avoids CORS issues

## Development Workflow

### Local Development (React App)

```bash
cd /Users/matthuewalsh/qseries-noise/Q_Web

# Start development server (hot reload)
npm run dev
# Access at: http://localhost:3000

# API server must be running separately:
cd ../Q_Sensor_API
uvicorn api.main:app --reload --port 8000
```

### Local Testing (Docker)

```bash
cd /Users/matthuewalsh/qseries-noise/Q_Sensor_API

# Build and run locally
docker build -t q-sensor-api:dev .
docker run -p 9150:9150 q-sensor-api:dev

# Access webapp at: http://localhost:9150/
```

## Troubleshooting

### Webapp shows blank page
- Check browser console for errors
- Verify `api/static/webapp/index.html` exists
- Verify assets are accessible: `curl http://localhost:9150/assets/index-*.js`

### API calls fail (404)
- Check CORS configuration in `api/main.py`
- Verify BlueOS proxy routing
- Check browser network tab for actual request URLs

### Assets not loading
- Verify FastAPI static file mounting in `api/main.py`:
  ```python
  app.mount("/assets", StaticFiles(directory="api/static/webapp/assets"), name="assets")
  ```
- Check file permissions: `ls -la api/static/webapp/assets/`

### Build errors
- Clear npm cache: `npm cache clean --force`
- Delete `node_modules` and reinstall: `rm -rf node_modules && npm install`
- Check Node.js version: `node --version` (requires v16+)

## File Structure

```
Q_Sensor_API/
└── api/
    └── static/
        ├── index.html              # Test page (legacy)
        ├── blueos-ui/              # Simple UI
        │   ├── index.html
        │   ├── style.css
        │   └── app.js
        └── webapp/                 # React production build
            ├── index.html          # Entry point (469B)
            └── assets/
                ├── index-*.css     # Bundled styles
                └── index-*.js      # Bundled JavaScript
```

## Notes

- **Hashed filenames** (`index-BnQj4ENf.js`) ensure cache busting on updates
- **Vite** automatically handles code splitting and optimization
- **React Router** uses browser history API (works with BlueOS proxy)
- **Zustand store** persists state across page refreshes (localStorage)
- **Build size**: ~150KB gzipped (production bundle)
