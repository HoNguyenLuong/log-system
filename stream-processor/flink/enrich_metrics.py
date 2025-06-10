import json
import requests
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.common.typeinfo import Types
from pyflink.datastream.connectors.kafka import FlinkKafkaConsumer, FlinkKafkaProducer
from pyflink.common.serialization import SimpleStringSchema
from ..config.kafka_config import KAFKA_BROKERS, KAFKA_SOURCE_TOPIC, KAFKA_SINK_TOPIC
from ..config.prometheus_config import PROMETHEUS_URL, PROM_QUERIES


# Dummy metadata store
SERVER_METADATA = {
    "192.168.1.25": {
        "os": "Ubuntu",
        "os_version": "20.04",
        "manager": {
            "user": "admin",
            "email": "admin@example.com"
        }
    }
}


def query_prometheus(ip, range_str):
    query = f"avg_over_time(node_load1{{instance='{ip}:9100'}}[{range_str}])"
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
        result = response.json()
        value = result["data"]["result"][0]["value"][1] if result["data"]["result"] else "N/A"
        return value
    except Exception:
        return "N/A"


def enrich_alert(raw_alert_json):
    try:
        alert = json.loads(raw_alert_json)
        ip = alert.get("server_ip")
        enriched = alert.copy()

        # Enrich static metadata
        metadata = SERVER_METADATA.get(ip, {})
        enriched.update({
            "os": metadata.get("os", "unknown"),
            "os_version": metadata.get("os_version", "unknown"),
            "manager_user": metadata.get("manager", {}).get("user", "unknown"),
            "manager_email": metadata.get("manager", {}).get("email", "unknown"),
        })

        # Enrich Prometheus metrics
        for key, prom_range in PROM_QUERIES.items():
            metric_val = query_prometheus(ip, prom_range)
            enriched[f"metric_{key}"] = metric_val

        return json.dumps(enriched)
    except Exception as e:
        print(f"[Error] Failed to enrich alert: {e}")
        return raw_alert_json


def main():
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(1)

    # Kafka Source
    kafka_source = FlinkKafkaConsumer(
        topics=KAFKA_SOURCE_TOPIC,
        deserialization_schema=SimpleStringSchema(),
        properties={
            "bootstrap.servers": KAFKA_BROKERS,
            "group.id": "flink-enrich-group",
        }
    )

    # Kafka Sink
    kafka_sink = FlinkKafkaProducer(
        topic=KAFKA_SINK_TOPIC,
        serialization_schema=SimpleStringSchema(),
        producer_config={
            "bootstrap.servers": KAFKA_BROKERS
        }
    )

    # Processing Pipeline
    ds = env.add_source(kafka_source).map(
        enrich_alert,
        output_type=Types.STRING()
    )

    ds.add_sink(kafka_sink)

    env.execute("Alert Enrichment Job")


if __name__ == "__main__":
    main()
