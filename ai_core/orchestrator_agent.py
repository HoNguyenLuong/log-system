# orchestrator_agent.py
import json
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
from base_agent import BaseAgent
from mcp_schema import MCPMessage, AlertSeverity
from elasticsearch import Elasticsearch
import re
from config import CLASSIFIER_ENDPOINT, EVALUATOR_ENDPOINT, ORCHESTRATOR_ENDPOINT, DEFAULT_TEMPERATURE
import asyncio

class OrchestratorAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(
            agent_name="orchestrator",
            model_name="mistral-7b-instruct-v0.1",  # Sử dụng cùng model với các agent khác
            **kwargs
        )
        self.es_client = Elasticsearch([{"host": "localhost", "port": 9200, "scheme": "http"}])

    def get_input_topics(self) -> List[str]:
        return ["enriched_alerts"]  # Nhận alert từ Prometheus Alertmanager

    def get_output_topics(self) -> Dict[str, str]:
        return {"default": "classifier_in"}

    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process and normalize alerts/logs to MCP format"""
        try:
            # Chuẩn bị context cho AI
            context = self._prepare_context(message)
            
            # Gọi AI để phân tích và chuẩn hóa
            system_prompt = self._get_orchestrator_system_prompt()
            prompt = self._build_orchestrator_prompt(context)
            
            # Thêm retry và error handling
            for attempt in range(3):
                try:
                    response = await self.call_lm_studio(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        api_url=ORCHESTRATOR_ENDPOINT,
                        temperature=0.1
                    )
                    if response:
                        break
                except Exception as e:
                    self.logger.error(f"API call attempt {attempt + 1} failed: {e}")
                    if attempt == 2:  # Last attempt
                        return self._create_fallback_mcp(message)
                    await asyncio.sleep(2 ** attempt)
                
            # Parse kết quả AI và tạo MCP message
            normalized_data = self._parse_ai_response(response, message)
            
            # Enrich with additional context
            enriched_message = await self._enrich_with_context(normalized_data)
            
            # Determine routing
            routing_decision = await self._determine_routing(enriched_message)
            enriched_message["agent_route"] = routing_decision

            return enriched_message

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            return self._create_fallback_mcp(message)

    def _prepare_context(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare context for AI analysis"""
        context = {
            "raw_message": message,
            "timestamp": datetime.now().isoformat(),
            "message_type": "alert" if self._is_prometheus_alert(message) else "log"
        }
        
        if self._is_prometheus_alert(message):
            context["alert_metadata"] = {
                "name": message.get("alertname"),
                "severity": message.get("labels", {}).get("severity"),
                "instance": message.get("labels", {}).get("instance"),
                "job": message.get("labels", {}).get("job")
            }
        
        return context

    def _get_orchestrator_system_prompt(self) -> str:
        """Load orchestrator system prompt"""
        with open('prompts/orchestrator_system.txt', 'r', encoding='utf-8') as f:
            return f.read()

    def _build_orchestrator_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for orchestrator"""
        with open('prompts/orchestrator_prompt.txt', 'r', encoding='utf-8') as f:
            prompt_template = f.read()
            
        return prompt_template.format(
            message_type=context["message_type"],
            raw_message=json.dumps(context["raw_message"], indent=2),
            timestamp=context["timestamp"]
        )

    def _parse_ai_response(self, response: str, original_message: Dict[str, Any]) -> Dict[str, Any]:
        """Parse AI response and create MCP message"""
        try:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if not json_match:
                return self._create_fallback_mcp(original_message)

            ai_result = json.loads(json_match.group())
            
            # Create MCP message with full alert details
            mcp_message = MCPMessage(
                id=original_message.get("alert_id", str(uuid.uuid4())),
                source="prometheus",
                agent_route="classifier",
                content={
                    "type": original_message.get("alert_type", "unknown"),
                    "alert_name": original_message.get("alertname"),
                    "alert_id": original_message.get("alert_id"),
                    "server_ip": original_message.get("server_ip"),
                    "severity": original_message.get("alert_level", AlertSeverity.MEDIUM),
                    "status": original_message.get("status", "firing"),
                    "message": original_message.get("message"),
                    "description": original_message.get("description"),
                    "labels": original_message.get("labels", {}),
                    "annotations": original_message.get("annotations", {}),
                    "starts_at": original_message.get("starts_at"),
                    "ends_at": original_message.get("ends_at"),
                    "created_at": original_message.get("created_at"),
                    "os_info": {
                        "os": original_message.get("os"),
                        "version": original_message.get("os_version")
                    },
                    "manager_info": {
                        "user": original_message.get("manager_user"),
                        "email": original_message.get("manager_email")
                    },
                    "metrics": {
                        "current_value": original_message.get("current_value"),
                        "load1": original_message.get("metric_load1")
                    },
                    "ai_analysis": {
                        "normalized_type": ai_result.get("normalized_type"),
                        "suggested_severity": ai_result.get("suggested_severity"),
                        "primary_component": ai_result.get("primary_component"),
                        "confidence_score": ai_result.get("confidence_score", 0.0),
                        "analysis_notes": ai_result.get("analysis_notes")
                    }
                }
            )

            return mcp_message.dict()

        except Exception as e:
            self.logger.error(f"Failed to parse AI response: {e}")
            return self._create_fallback_mcp(original_message)

    def _create_fallback_mcp(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Create fallback MCP message when AI processing fails"""
        return MCPMessage(
            id=str(uuid.uuid4()),
            source="prometheus",
            agent_route="classifier",
            content={
                "type": "alert",
                "alert_name": message.get("alertname", "Unknown"),
                "severity": AlertSeverity.MEDIUM,
                "status": message.get("status", "firing"),
                "labels": message.get("labels", {}),
                "annotations": message.get("annotations", {})
            }
        ).dict()

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