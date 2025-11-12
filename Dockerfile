FROM python:3.10

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api/ ./api/
COPY q_sensor_lib/ ./q_sensor_lib/
COPY data_store/ ./data_store/

# Expose port
EXPOSE 9150

# Set environment variables with defaults
ENV SERIAL_PORT=/dev/ttyUSB0
ENV SERIAL_BAUD=9600
ENV LOG_LEVEL=INFO
ENV CHUNK_RECORDING_PATH=/data/qsensor_recordings

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9150')"

# Run uvicorn
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "9150"]
