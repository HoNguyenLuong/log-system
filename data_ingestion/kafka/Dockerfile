# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY kafka/kafka_webhook_bridge.py .


# Install dependencies
RUN pip install --no-cache-dir flask kafka-python

# Expose API port
EXPOSE 8000

# Start the webhook app
CMD ["python", "kafka_webhook_bridge.py"]
