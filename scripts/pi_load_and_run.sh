#!/bin/bash
# Transfer Docker image to Pi and run Q-Sensor API

set -e

PI_HOST="pi@blueos.local"
IMAGE_TAR="$HOME/q-sensor-api-dev.tar"
IMAGE="q-sensor-api:dev"
CONTAINER_NAME="q-sensor-api"

echo "Deploying Q-Sensor API to BlueOS Pi..."
echo ""

# Step 1: Transfer image
echo "[1/4] Transferring Docker image to Pi..."
scp $IMAGE_TAR $PI_HOST:~/

# Step 2: Load image
echo ""
echo "[2/4] Loading Docker image on Pi..."
ssh $PI_HOST "docker load < ~/q-sensor-api-dev.tar"

# Step 3: Stop existing container if running
echo ""
echo "[3/4] Stopping existing container (if any)..."
ssh $PI_HOST "docker stop $CONTAINER_NAME 2>/dev/null || true"
ssh $PI_HOST "docker rm $CONTAINER_NAME 2>/dev/null || true"

# Step 4: Run new container
echo ""
echo "[4/4] Starting Q-Sensor API container..."

# Check if serial device exists
DEVICE_FLAG=""
if ssh $PI_HOST "test -e /dev/ttyUSB0"; then
  DEVICE_FLAG="--device /dev/ttyUSB0:/dev/ttyUSB0"
  echo "Serial device /dev/ttyUSB0 found, mapping to container"
else
  echo "Note: /dev/ttyUSB0 not found, skipping device mapping"
fi

ssh $PI_HOST "docker run -d \
  --network host \
  $DEVICE_FLAG \
  -v /usr/blueos/userdata/q_sensor:/data \
  -e SERIAL_PORT=/dev/ttyUSB0 \
  -e SERIAL_BAUD=9600 \
  -e LOG_LEVEL=INFO \
  --name $CONTAINER_NAME \
  --restart unless-stopped \
  $IMAGE"

echo ""
echo "✓ Deployment complete!"
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
echo "  ssh $PI_HOST 'docker logs -f $CONTAINER_NAME'"
