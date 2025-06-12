#!/usr/bin/env python3
"""
Kafka Webhook Bridge - Receives alerts from AlertManager and sends to Kafka
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime
from flask import Flask, request, jsonify
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Kafka configuration
KAFKA_BOOTSTRAP_SERVERS = 'kafka1:9092,kafka2:9092,kafka3:9092'

ALERT_TOPIC = 'raw_alerts'

# Initialize producer later
producer = None


def get_kafka_producer(retries=5, delay=5):
    """Try connecting to Kafka with retries"""
    for attempt in range(retries):
        try:
            p = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                retries=3,
                acks='all'
            )
            logger.info(f"Kafka Producer connected to: {KAFKA_BOOTSTRAP_SERVERS}")
            return p
        except Exception as e:
            logger.warning(f"[Attempt {attempt + 1}/{retries}] Kafka connection failed: {e}")
            time.sleep(delay)
    raise Exception("Could not connect to Kafka after several attempts.")


def create_kafka_topic(topic_name, num_partitions=3, replication_factor=3):
    """Create Kafka topic if it doesn't exist"""
    try:
        admin_client = KafkaAdminClient(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            client_id='kafka-webhook-admin'
        )
        topic = NewTopic(
            name=topic_name,
            num_partitions=num_partitions,
            replication_factor=replication_factor
        )
        admin_client.create_topics(new_topics=[topic], validate_only=False)
        logger.info(f"Created Kafka topic '{topic_name}' with {num_partitions} partitions and {replication_factor} replicas.")
    except TopicAlreadyExistsError:
        logger.info(f"Kafka topic '{topic_name}' already exists. Skipping creation.")
    except Exception as e:
        logger.warning(f"Failed to create topic '{topic_name}': {e}")


def create_alert_message(alert_data):
    """Convert AlertManager webhook data to alert format"""
    alerts = []

    for alert in alert_data.get('alerts', []):
        server_ip = alert.get('labels', {}).get('server_ip', 'unknown')
        alert_type = alert.get('labels', {}).get('alert_type', 'unknown')
        severity = alert.get('labels', {}).get('severity', 'info')

        alert_message = {
            'alert_id': str(uuid.uuid4()),
            'server_ip': server_ip,
            'alert_type': alert_type,
            'alert_level': severity.upper(),
            'alertname': alert.get('labels', {}).get('alertname', ''),
            'message': alert.get('annotations', {}).get('summary', ''),
            'description': alert.get('annotations', {}).get('description', ''),
            'current_value': alert.get('annotations', {}).get('current_value', ''),
            'labels': alert.get('labels', {}),
            'annotations': alert.get('annotations', {}),
            'status': alert.get('status', 'firing'),
            'starts_at': alert.get('startsAt', ''),
            'ends_at': alert.get('endsAt', ''),
            'generator_url': alert.get('generatorURL', ''),
            'fingerprint': alert.get('fingerprint', ''),
            'created_at': datetime.utcnow().isoformat()
        }

        if alert_type in ['cpu_alert', 'memory_alert', 'disk_alert']:
            try:
                if alert_message['current_value']:
                    value_str = alert_message['current_value'].replace('%', '')
                    alert_message['metric_value'] = float(value_str)
                    alert_message['threshold_value'] = 90.0
            except (ValueError, TypeError):
                logger.warning(f"Could not parse metric value: {alert_message['current_value']}")

        alerts.append(alert_message)

    return alerts


@app.route('/webhook/alert', methods=['POST'])
def receive_alert():
    """Receive alert from AlertManager and send to Kafka"""
    try:
        alert_data = request.get_json()
        if not alert_data:
            return jsonify({'error': 'No data received'}), 400

        logger.info(f"Received webhook: status={alert_data.get('status')} alerts={len(alert_data.get('alerts', []))}")
        alerts = create_alert_message(alert_data)

        for alert in alerts:
            try:
                future = producer.send(ALERT_TOPIC, value=alert)
                record_metadata = future.get(timeout=10)
                logger.info(
                    f"Alert sent to Kafka: topic={record_metadata.topic}, partition={record_metadata.partition}, offset={record_metadata.offset}")
            except Exception as e:
                logger.error(f"Failed to send alert to Kafka: {e}")
                return jsonify({'error': f'Failed to send to Kafka: {str(e)}'}), 500

        return jsonify({'status': 'success', 'alerts_processed': len(alerts)}), 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/send-to-kafka', methods=['POST'])
def send_to_kafka():
    """Generic endpoint to send arbitrary data to Kafka"""
    try:
        data = request.get_json()
        topic = request.headers.get('X-Kafka-Topic', ALERT_TOPIC)

        future = producer.send(topic, value=data)
        logger.info(f"Sending generic data to topic '{topic}' on cluster {KAFKA_BOOTSTRAP_SERVERS}")
        record_metadata = future.get(timeout=10)

        return jsonify({
            'status': 'success',
            'topic': record_metadata.topic,
            'partition': record_metadata.partition,
            'offset': record_metadata.offset
        }), 200

    except Exception as e:
        logger.error(f"Error sending to Kafka: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'service': 'kafka-webhook-bridge'}), 200


if __name__ == '__main__':
    logger.info("Starting Kafka Webhook Bridge...")

    # Delay to ensure Kafka is ready
    time.sleep(5)

    # 🔧 Create topic if it doesn't exist
    create_kafka_topic(ALERT_TOPIC, num_partitions=3, replication_factor=3)

    # ⚙️ Connect producer
    producer = get_kafka_producer()

    # 🚀 Start Flask app
    port = int(os.getenv("PORT", 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
