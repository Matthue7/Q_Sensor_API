#!/bin/bash
# Build Docker image directly on the Pi (recommended for architecture compatibility)

set -e

PI_HOST="pi@blueos.local"
PROJECT_NAME="Q_Sensor_API"
REMOTE_DIR="/home/pi/q-sensor-api"

echo "Building Q-Sensor API directly on Pi..."
echo ""

# Step 1: Clean and create remote directory
echo "[1/5] Setting up remote directory..."
ssh $PI_HOST "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR"

# Step 2: Copy necessary files
echo ""
echo "[2/5] Transferring files to Pi..."
rsync -avz --relative --exclude='*.pyc' --exclude='__pycache__' --exclude='.git' \
  --exclude='*.tar' --exclude='venv' --exclude='.pytest_cache' --exclude='tests' \
  --exclude='pi_hw_tests' --exclude='fakes' --exclude='scripts' \
  ./Dockerfile ./requirements.txt ./api/ ./q_sensor_lib/ ./data_store/ \
  $PI_HOST:$REMOTE_DIR/

# Step 3: Build image on Pi
echo ""
echo "[3/5] Building Docker image on Pi (this may take a few minutes)..."
ssh $PI_HOST "cd $REMOTE_DIR && docker build -t q-sensor-api:dev ."

# Step 4: Stop existing container if running
echo ""
echo "[4/5] Stopping existing container (if any)..."
ssh $PI_HOST "docker stop q-sensor-api 2>/dev/null || true"
ssh $PI_HOST "docker rm q-sensor-api 2>/dev/null || true"

# Step 5: Run new container
echo ""
echo "[5/5] Starting Q-Sensor API container..."

# Check if serial device exists
DEVICE_FLAG=""
ssh $PI_HOST "test -e /dev/ttyUSB0" && DEVICE_FLAG="--device /dev/ttyUSB0:/dev/ttyUSB0" || echo "Note: /dev/ttyUSB0 not found, skipping device mapping"

ssh $PI_HOST "docker run -d \
  --network host \
  $DEVICE_FLAG \
  -v /usr/blueos/userdata/q_sensor:/data \
  -e SERIAL_PORT=/dev/ttyUSB0 \
  -e SERIAL_BAUD=9600 \
  -e LOG_LEVEL=INFO \
  --name q-sensor-api \
  --restart unless-stopped \
  q-sensor-api:dev"

echo ""
echo "✓ Build and deployment complete!"
echo ""
echo "Container status:"
ssh $PI_HOST "docker ps | grep q-sensor"

echo ""
echo "Access the API:"
echo "  Swagger UI:  http://blueos.local:9150/docs"
echo "  Health:      curl http://blueos.local:9150/"
echo "  Register:    curl http://blueos.local:9150/register_service"
echo ""
echo "View logs:"
echo "  ssh $PI_HOST 'docker logs -f q-sensor-api'"
