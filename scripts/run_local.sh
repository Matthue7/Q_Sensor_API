#!/bin/bash
# Run Q-Sensor API Docker container locally

set -e

IMAGE="q-sensor-api:dev"

echo "Starting Q-Sensor API container..."
echo "Port: 9150"
echo "Image: $IMAGE"
echo ""

docker run --rm \
  -p 9150:9150 \
  -e SERIAL_PORT=/dev/ttyUSB0 \
  -e SERIAL_BAUD=9600 \
  -e LOG_LEVEL=INFO \
  --name q-sensor-api \
  $IMAGE

# Note: Add --device /dev/ttyUSB0:/dev/ttyUSB0 if you have a serial device connected
