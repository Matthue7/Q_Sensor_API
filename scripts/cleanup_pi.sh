#!/bin/bash
# Clean up old build artifacts on Pi

PI_HOST="pi@blueos.local"

echo "Cleaning up Q-Sensor API artifacts on Pi..."
echo ""

echo "[1/3] Stopping and removing container..."
ssh $PI_HOST "docker stop q-sensor-api 2>/dev/null || true"
ssh $PI_HOST "docker rm q-sensor-api 2>/dev/null || true"

echo ""
echo "[2/3] Removing old directories and tar files..."
ssh $PI_HOST "rm -rf ~/q-sensor-api ~/Q_Sensor_API ~/q-sensor-api-dev.tar"

echo ""
echo "[3/3] Removing old Docker image..."
ssh $PI_HOST "docker rmi q-sensor-api:dev 2>/dev/null || true"

echo ""
echo "✓ Cleanup complete!"
echo ""
echo "Next step: Run ./scripts/build_on_pi.sh to do a fresh build"
