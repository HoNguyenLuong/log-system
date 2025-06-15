from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    from_json, col, to_timestamp, to_date, count, when
)
from pyspark.sql.types import StructType, StringType, BooleanType

# 1. Spark session
spark = SparkSession.builder \
    .appName("AlertAnalyticsJob") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.7.3") \
    .getOrCreate()

# 2. Schema của JSON alert
alert_schema = StructType() \
    .add("timestamp", StringType()) \
    .add("server_ip", StringType()) \
    .add("alert_type", StringType()) \
    .add("alert_level", StringType()) \
    .add("alertname", StringType()) \
    .add("message", StringType()) \
    .add("resolved", BooleanType())

# 3. Đọc stream từ Kafka
kafka_df = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "localhost:9192") \
    .option("subscribe", "alerts") \
    .option("startingOffsets", "earliest") \
    .load()

# 4. Parse JSON
alerts_df = kafka_df.selectExpr("CAST(value AS STRING)") \
    .select(from_json(col("value"), alert_schema).alias("data")) \
    .select("data.*") \
    .withColumn("timestamp", to_timestamp("timestamp"))

# 5. Các tính toán chính
alert_type_summary = alerts_df.groupBy("alert_type") \
    .agg(count("*").alias("count"))

resolved_summary = alerts_df.groupBy("resolved") \
    .agg(count("*").alias("count"))

alert_by_day = alerts_df.withColumn("day", to_date("timestamp")) \
    .groupBy("day") \
    .agg(count("*").alias("total_alerts"))

server_alert_summary = alerts_df.groupBy("server_ip", "alert_type") \
    .agg(count("*").alias("count"))

# 6. Kết nối PostgreSQL
pg_url = "jdbc:postgresql://localhost:5432/superset"
pg_properties = {
    "user": "superset",
    "password": "superset",
    "driver": "org.postgresql.Driver"
}

def write_to_postgres(df, epoch_id, table_name):
    df.write \
        .jdbc(url=pg_url, table=table_name, mode="overwrite", properties=pg_properties)

# 7. Ghi vào PostgreSQL
query1 = alert_type_summary.writeStream \
    .outputMode("complete") \
    .foreachBatch(lambda df, eid: write_to_postgres(df, eid, "alert_type_summary")) \
    .start()

query2 = resolved_summary.writeStream \
    .outputMode("complete") \
    .foreachBatch(lambda df, eid: write_to_postgres(df, eid, "resolved_summary")) \
    .start()

query3 = alert_by_day.writeStream \
    .outputMode("complete") \
    .foreachBatch(lambda df, eid: write_to_postgres(df, eid, "alert_by_day")) \
    .start()

query4 = server_alert_summary.writeStream \
    .outputMode("complete") \
    .foreachBatch(lambda df, eid: write_to_postgres(df, eid, "server_alert_summary")) \
    .start()

# 8. Chờ các query kết thúc
spark.streams.awaitAnyTermination()
