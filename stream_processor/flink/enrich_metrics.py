import json
import requests
import traceback
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.common.typeinfo import Types
from pyflink.datastream.connectors.kafka import FlinkKafkaConsumer, FlinkKafkaProducer
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common import Configuration # Kept for potential future use, but not for pipeline.jars here

# --- Dummy Config Values (Replace with your actual imports from config files) ---
# Assuming these are defined in your config files:
# from stream_processor.config.kafka_config import KAFKA_BROKERS, KAFKA_SOURCE_TOPIC, KAFKA_SINK_TOPIC
# from stream_processor.config.prometheus_config import PROMETHEUS_URL, PROM_QUERIES

# For standalone execution, if config files are not available:
KAFKA_BROKERS = "localhost:9192"
KAFKA_SOURCE_TOPIC = "raw_alerts"
KAFKA_SINK_TOPIC = "enriched_alerts"
PROMETHEUS_URL = "http://localhost:9090"
PROM_QUERIES = {"load1": "5m"} # Example query: avg_over_time for 5 minutes
# -----------------------------------------------------------------------------


# Dummy metadata store
SERVER_METADATA = {
    "192.168.1.7": {
        "os": "Ubuntu",
        "os_version": "20.04",
        "manager": {
            "user": "admin",
            "email": "admin@example.com"
        }
    }
}

def query_prometheus(ip, range_str):
    """
    Queries Prometheus for a specific metric (e.g., node_load1) for a given IP.
    Returns the metric value or "N/A" if an error occurs.
    """
    # Construct the Prometheus query string
    query = f"avg_over_time(node_load1{{instance='{ip}:9100'}}[{range_str}])"
    try:
        # Make a GET request to the Prometheus API
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
        # Raise an HTTPError for bad responses (4xx or 5xx status codes)
        response.raise_for_status()
        result = response.json()

        # Extract the metric value. Prometheus response structure:
        # data -> result (list) -> [0] (first element) -> value (list) -> [1] (actual value)
        value = "N/A"
        if result["data"]["result"]:
            value = result["data"]["result"][0]["value"][1]
        return value
    except requests.exceptions.RequestException as e:
        # Catch specific requests exceptions (network errors, timeouts, HTTP errors)
        print(f"[Error] Prometheus query failed for IP {ip} with query '{query}': {e}")
        return "N/A"
    except (IndexError, KeyError) as e:
        # Catch errors if the JSON structure from Prometheus is unexpected
        print(f"[Error] Failed to parse Prometheus response for IP {ip}: {e}. Response: {result}")
        return "N/A"
    except Exception as e:
        # Catch any other unexpected exceptions
        print(f"[Error] An unexpected error occurred during Prometheus query for IP {ip}: {e}")
        return "N/A"


def enrich_alert(raw_alert_json):
    try:
        alert = json.loads(raw_alert_json)

        # Fallback IP extraction logic
        ip = alert.get("server_ip")
        if ip == "unknown" or not ip:
            # Try to get from labels.instance (e.g., "192.168.1.7:9100") → strip port
            instance = alert.get("labels", {}).get("instance", "")
            ip = instance.split(":")[0] if instance else None

        enriched = alert.copy()

        # --- Enrich with static metadata ---
        metadata = SERVER_METADATA.get(ip, {}) if ip else {}
        enriched.update({
            "os": metadata.get("os", "unknown"),
            "os_version": metadata.get("os_version", "unknown"),
            "manager_user": metadata.get("manager", {}).get("user", "unknown"),
            "manager_email": metadata.get("manager", {}).get("email", "unknown"),
        })

        # --- Enrich with Prometheus metrics ---
        if ip:
            for key, prom_range in PROM_QUERIES.items():
                metric_val = query_prometheus(ip, prom_range)
                enriched[f"metric_{key}"] = metric_val
        else:
            print(f"[Warning] Không tìm thấy IP trong alert: {raw_alert_json}")

        return json.dumps(enriched)

    except json.JSONDecodeError as e:
        print(f"[Error] Failed to decode JSON alert: {e}. Raw input: {raw_alert_json}")
        return raw_alert_json
    except Exception as e:
        print(f"[Error] Failed to enrich alert: {e}. Raw input: {raw_alert_json}")
        return raw_alert_json



def main():
    """
    Main function to define and execute the Flink streaming job.
    This job consumes raw alerts from Kafka, enriches them, and publishes
    the enriched alerts back to Kafka.
    """
    # 1. Get the StreamExecutionEnvironment
    # This is the entry point for creating and executing Flink streaming jobs.
    env = StreamExecutionEnvironment.get_execution_environment()

    # 2. Add the Kafka connector JAR to the Flink job's classpath
    # This is CRUCIAL for FlinkKafkaConsumer and FlinkKafkaProducer to work.
    # Replace the path with the actual location of your Flink Kafka connector JAR.
    # Make sure the JAR version matches your Flink version.
    env.add_jars("file:///D:/Viettel/connector/flink-connector-kafka-3.2.0-1.19.jar")
    env.add_jars("file:///D:/Viettel/connector/kafka-clients-3.3.2.jar")

    # 3. Set job parallelism (e.g., 1 for single-threaded processing)
    env.set_parallelism(1)

    # 4. Set global job parameters (optional, but good for configuration)
    # These parameters can be accessed within UDFs (User Defined Functions)
    # The set_global_job_parameters method expects a dictionary.
    env.get_config().set_global_job_parameters({"env": "dev", "source": "kafka"})


    # 5. Define Kafka Source (consumer)
    # Reads messages from the specified Kafka topic(s)
    kafka_source = FlinkKafkaConsumer(
        topics=KAFKA_SOURCE_TOPIC,
        deserialization_schema=SimpleStringSchema(), # Defines how to deserialize incoming bytes to strings
        properties={
            "bootstrap.servers": KAFKA_BROKERS, # Kafka broker addresses
            "group.id": "flink-enrich-group-v2", # Consumer group ID
            "auto.offset.reset": "earliest" # Start reading from the beginning if no committed offset
        }
    )

    # 6. Define Kafka Sink (producer)
    # Writes messages to the specified Kafka topic
    kafka_sink = FlinkKafkaProducer(
        topic=KAFKA_SINK_TOPIC,
        serialization_schema=SimpleStringSchema(), # Defines how to serialize outgoing strings to bytes
        producer_config={
            "bootstrap.servers": KAFKA_BROKERS # Kafka broker addresses
        }
    )

    # 7. Build the Processing Pipeline
    # a. Add the Kafka source to the environment to create a DataStream
    ds = env.add_source(kafka_source)

    # b. Apply the enrichment logic using a map transformation
    # The enrich_alert function will be called for each record in the stream.
    # output_type=Types.STRING() tells Flink the expected output type of the map function.
    enriched_ds = ds.map(
        enrich_alert,
        output_type=Types.STRING()
    )

    # c. Add the Kafka sink to the enriched DataStream
    # This will publish the enriched records to the Kafka sink topic.
    enriched_ds.print()
    enriched_ds.add_sink(kafka_sink)

    # 8. Execute the Flink job
    # This call is blocking and will submit the job to the Flink cluster.
    print(f"Starting Flink job 'Alert Enrichment Job'. Reading from '{KAFKA_SOURCE_TOPIC}', writing to '{KAFKA_SINK_TOPIC}'.")
    try:
        env.execute("Alert Enrichment Job")
    except Exception as e:
        print("❌ Flink job failed with exception:")
        traceback.print_exc()
    print("Flink job finished.")


if __name__ == "__main__":
    main()

