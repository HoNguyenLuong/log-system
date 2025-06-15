# orchestrator_agent.py
import json
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
from base_agent import BaseAgent
from mcp_schema import MCPMessage, AlertSeverity
from elasticsearch import Elasticsearch


class OrchestratorAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(
            agent_name="orchestrator",
            model_name="phi3.5:3.8b",
            **kwargs
        )
        self.es_client = Elasticsearch([{"host": "localhost", "port": 9200}])

    def get_input_topics(self) -> List[str]:
        return ["enriched_alerts"]

    def get_output_topics(self) -> Dict[str, str]:
        return {"default": "classifier_in"}

    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process and normalize alerts/logs to MCP format"""

        try:
            # Detect message type and normalize
            if self._is_prometheus_alert(message):
                mcp_message = await self._normalize_prometheus_alert(message)
            elif self._is_elasticsearch_log(message):
                mcp_message = await self._normalize_elasticsearch_log(message)
            else:
                self.logger.warning(f"Unknown message format: {list(message.keys())}")
                return None

            # Enrich with context
            enriched_message = await self._enrich_with_context(mcp_message)

            # AI-powered routing decision
            routing_decision = await self._determine_routing(enriched_message)
            enriched_message["agent_route"] = routing_decision

            return enriched_message

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            return None

    def _is_prometheus_alert(self, message: Dict[str, Any]) -> bool:
        """Check if message is from Prometheus"""
        return any(key in message for key in ["alertname", "labels", "annotations", "status"])

    def _is_elasticsearch_log(self, message: Dict[str, Any]) -> bool:
        """Check if message is from Elasticsearch"""
        return any(key in message for key in ["@timestamp", "log_level", "message", "service"])

    async def _normalize_prometheus_alert(self, alert_data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Prometheus alert to MCP format"""

        alert_name = alert_data.get("alertname", "Unknown")
        labels = alert_data.get("labels", {})
        annotations = alert_data.get("annotations", {})

        # Smart severity mapping
        severity = self._map_severity(labels, annotations, alert_name)

        mcp_message = MCPMessage(
            id=str(uuid.uuid4()),
            source="prometheus",
            agent_route="classifier",  # Will be determined by AI
            content={
                "type": "alert",
                "alert_name": alert_name,
                "labels": labels,
                "annotations": annotations,
                "severity": severity,
                "status": alert_data.get("status", "firing")
            }
        )

        return mcp_message.dict()

    async def _normalize_elasticsearch_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Elasticsearch log to MCP format"""

        mcp_message = MCPMessage(
            id=str(uuid.uuid4()),
            source="elasticsearch",
            agent_route="classifier",
            content={
                "type": "log",
                "log_level": log_data.get("log_level", log_data.get("level", "info")),
                "message": log_data.get("message", ""),
                "service": log_data.get("service", log_data.get("service_name", "unknown")),
                "host": log_data.get("host", log_data.get("hostname", "unknown")),
                "timestamp": log_data.get("@timestamp", datetime.utcnow().isoformat())
            }
        )

        return mcp_message.dict()

    def _map_severity(self, labels: Dict, annotations: Dict, alert_name: str) -> str:
        """Smart severity mapping using multiple indicators"""

        # Direct severity from labels
        if "severity" in labels:
            severity_map = {
                "critical": AlertSeverity.CRITICAL,
                "high": AlertSeverity.HIGH,
                "warning": AlertSeverity.MEDIUM,
                "info": AlertSeverity.INFO
            }
            return severity_map.get(labels["severity"].lower(), AlertSeverity.MEDIUM)

        # Infer from alert name
        alert_name_lower = alert_name.lower()
        if "critical" in alert_name_lower or "down" in alert_name_lower:
            return AlertSeverity.CRITICAL
        elif "high" in alert_name_lower or "error" in alert_name_lower:
            return AlertSeverity.HIGH
        elif "warning" in alert_name_lower:
            return AlertSeverity.MEDIUM

        return AlertSeverity.MEDIUM

    async def _enrich_with_context(self, mcp_message: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich message with contextual information"""

        try:
            context = {}
            content = mcp_message.get("content", {})

            if mcp_message.get("source") == "prometheus":
                # Get related logs
                labels = content.get("labels", {})
                host = labels.get("instance", labels.get("hostname", ""))

                if host:
                    related_logs = await self._get_related_logs(host, minutes=5)
                    context["recent_logs"] = related_logs[:3]  # Top 3 most relevant

            elif mcp_message.get("source") == "elasticsearch":
                # Get related alerts
                host = content.get("host", "")
                service = content.get("service", "")

                if host or service:
                    related_patterns = await self._get_log_patterns(host, service)
                    context["log_patterns"] = related_patterns

            # Add historical context
            context["historical_frequency"] = await self._get_historical_frequency(content)

            mcp_message["context"] = context

        except Exception as e:
            self.logger.warning(f"Context enrichment failed: {e}")

        return mcp_message

    async def _get_related_logs(self, host: str, minutes: int = 5) -> List[Dict]:
        """Get related logs from Elasticsearch"""
        try:
            query = {
                "query": {
                    "bool": {
                        "must": [
                            {"range": {"@timestamp": {"gte": f"now-{minutes}m"}}},
                            {"term": {"host.keyword": host}}
                        ],
                        "should": [
                            {"term": {"log_level": "error"}},
                            {"term": {"log_level": "warning"}}
                        ]
                    }
                },
                "size": 5,
                "sort": [{"@timestamp": {"order": "desc"}}]
            }

            response = self.es_client.search(index="logs-*", body=query)
            return [hit["_source"] for hit in response["hits"]["hits"]]

        except Exception as e:
            self.logger.warning(f"Failed to get related logs: {e}")
            return []

    async def _get_log_patterns(self, host: str, service: str) -> Dict:
        """Analyze log patterns"""
        try:
            # Simple pattern analysis
            query = {
                "query": {
                    "bool": {
                        "must": [
                            {"range": {"@timestamp": {"gte": "now-1h"}}},
                        ]
                    }
                },
                "aggs": {
                    "error_count": {
                        "filter": {"term": {"log_level": "error"}}
                    },
                    "warning_count": {
                        "filter": {"term": {"log_level": "warning"}}
                    }
                }
            }

            if host:
                query["query"]["bool"]["must"].append({"term": {"host.keyword": host}})
            if service:
                query["query"]["bool"]["must"].append({"term": {"service.keyword": service}})

            response = self.es_client.search(index="logs-*", body=query)

            return {
                "error_count_1h": response["aggregations"]["error_count"]["doc_count"],
                "warning_count_1h": response["aggregations"]["warning_count"]["doc_count"],
                "total_logs_1h": response["hits"]["total"]["value"]
            }

        except Exception as e:
            self.logger.warning(f"Pattern analysis failed: {e}")
            return {}

    async def _get_historical_frequency(self, content: Dict) -> Dict:
        """Get historical frequency of similar alerts/logs"""
        # Simple implementation - can be enhanced with ML
        return {
            "frequency": "medium",  # low, medium, high
            "last_occurrence": "unknown",
            "trend": "stable"  # increasing, decreasing, stable
        }

    async def _determine_routing(self, mcp_message: Dict[str, Any]) -> str:
        """AI-powered routing decision"""

        # For now, all messages go to classifier
        # Can be enhanced with more sophisticated routing logic
        content = mcp_message.get("content", {})

        # Simple rule-based routing
        if content.get("type") == "alert":
            alert_name = content.get("alert_name", "").lower()
            if "security" in alert_name or "auth" in alert_name:
                return "security_classifier"
            elif "performance" in alert_name or "cpu" in alert_name or "memory" in alert_name:
                return "performance_classifier"

        return "classifier"  # Default route