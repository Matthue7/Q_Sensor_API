#!/bin/bash
# Save Docker image to tarball for transfer to Pi

set -e

IMAGE="q-sensor-api:dev"
OUTPUT="$HOME/q-sensor-api-dev.tar"

echo "Saving Docker image to tarball..."
echo "Image: $IMAGE"
echo "Output: $OUTPUT"
echo ""

docker save $IMAGE > $OUTPUT

SIZE=$(du -h $OUTPUT | cut -f1)
echo ""
echo "✓ Image saved successfully!"
echo "File: $OUTPUT"
echo "Size: $SIZE"
echo ""
echo "Next step:"
echo "  Deploy to Pi:  ./scripts/pi_load_and_run.sh"
