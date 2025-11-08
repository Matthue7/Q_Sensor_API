#!/bin/bash
# Build Docker image locally for Q-Sensor API

set -e

echo "Building q-sensor-api:dev Docker image..."
docker build -t q-sensor-api:dev .

echo ""
echo "✓ Build complete!"
echo "Image: q-sensor-api:dev"
echo ""
echo "Next steps:"
echo "  Run locally:  ./scripts/run_local.sh"
echo "  Save for Pi:  ./scripts/save_image.sh"
