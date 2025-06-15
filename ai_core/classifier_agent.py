# classifier_reasoner_agent.py
import json
import re
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
from base_agent import BaseAgent
from mcp_schema import ClassificationResult, AlertCategory, AlertSeverity, FeedbackMessage
from config import CLASSIFIER_ENDPOINT, EVALUATOR_ENDPOINT, ORCHESTRATOR_ENDPOINT, DEFAULT_TEMPERATURE



class ClassifierReasonerAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(
            agent_name="classifier_reasoner",
            # model_name="qwen2.5:14b-instruct",
            model_name="tinyllama-1.1b-chat-v1.0",
            **kwargs
        )
        self.feedback_memory = {}  # Store feedback for continuous learning
        self.classification_patterns = {}  # Store successful patterns

    def get_input_topics(self) -> List[str]:
        return ["classifier_in", "classifier_feedback"]

    def get_output_topics(self) -> Dict[str, str]:
        return {"default": "evaluator_in"}

    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process classification request or feedback"""

        # Check if this is feedback message
        if "was_misclassified" in message:
            return await self._process_feedback(message)

        # Regular classification
        return await self._classify_message(message)

    async def _classify_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Classify alert/log using AI reasoning"""

        try:
            message_id = message.get("id")
            content = message.get("content", {})
            context = message.get("context", {})

            # Prepare classification context
            classification_context = self._prepare_classification_context(message)

            # Get relevant feedback for learning
            feedback_context = self._get_relevant_feedback(content)

            # Build comprehensive prompt
            prompt = self._build_classification_prompt(
                content, context, classification_context, feedback_context
            )

            # Get AI classification
            system_prompt = self._get_classification_system_prompt()
            response = await self.call_lm_studio(prompt, system_prompt, CLASSIFIER_ENDPOINT, temperature=0.1)

            # Parse structured response
            classification = self._parse_classification_response(response, message_id)

            # Store pattern for future reference
            self._store_classification_pattern(content, classification)

            return classification.dict()

        except Exception as e:
            self.logger.error(f"Classification failed: {e}")
            return self._create_fallback_classification(message)

    def _get_classification_system_prompt(self) -> str:
        """Load system prompt from file"""
        with open('prompts/classification_system.txt', 'r', encoding='utf-8') as f:
            return f.read()

    def _prepare_classification_context(self, message: Dict[str, Any]) -> str:
        """Prepare rich context for classification"""

        content = message.get("content", {})
        context = message.get("context", {})
        source = message.get("source", "")

        context_parts = [f"Source: {source}"]

        if source == "prometheus":
            alert_name = content.get("alert_name", "")
            labels = content.get("labels", {})
            annotations = content.get("annotations", {})
            status = content.get("status", "")

            context_parts.extend([
                f"Alert: {alert_name}",
                f"Status: {status}",
                f"Labels: {self._format_dict(labels)}",
                f"Annotations: {self._format_dict(annotations)}"
            ])

        elif source == "elasticsearch":
            log_level = content.get("log_level", "")
            message_text = content.get("message", "")
            service = content.get("service", "")
            host = content.get("host", "")

            context_parts.extend([
                f"Log Level: {log_level}",
                f"Service: {service}",
                f"Host: {host}",
                f"Message: {message_text}"
            ])

        # Add contextual information
        if context:
            if "recent_logs" in context:
                context_parts.append("Recent Related Logs:")
                for log in context["recent_logs"]:
                    context_parts.append(f"  - {log.get('log_level', '')}: {log.get('message', '')}")

            if "log_patterns" in context:
                patterns = context["log_patterns"]
                context_parts.append(
                    f"Recent Activity: {patterns.get('error_count_1h', 0)} errors, {patterns.get('warning_count_1h', 0)} warnings in last hour")

            if "historical_frequency" in context:
                freq_info = context["historical_frequency"]
                context_parts.append(
                    f"Historical Pattern: {freq_info.get('frequency', 'unknown')} frequency, trend: {freq_info.get('trend', 'unknown')}")

        return "\n".join(context_parts)

    def _format_dict(self, d: Dict[str, Any]) -> str:
        """Format dictionary for readable output"""
        if not d:
            return "{}"
        items = [f"{k}={v}" for k, v in d.items()]
        return "{" + ", ".join(items) + "}"

    def _get_relevant_feedback(self, content: Dict[str, Any]) -> str:
        """Get relevant feedback from memory for similar cases"""

        # Simple similarity matching - can be enhanced with embeddings
        relevant_feedback = []

        for feedback_id, feedback in self.feedback_memory.items():
            if self._is_similar_content(content, feedback.get("original_content", {})):
                relevant_feedback.append(feedback)

        if relevant_feedback:
            feedback_text = "Previous Feedback:\n"
            for fb in relevant_feedback[-3:]:  # Last 3 relevant feedbacks
                feedback_text += f"- {fb.get('reason', '')}\n"
            return feedback_text

        return ""

    def _is_similar_content(self, content1: Dict, content2: Dict) -> bool:
        """Simple similarity check - can be enhanced"""
        if content1.get("type") != content2.get("type"):
            return False

        if content1.get("type") == "alert":
            return content1.get("alert_name") == content2.get("alert_name")
        elif content1.get("type") == "log":
            return content1.get("service") == content2.get("service")

        return False

    def _build_classification_prompt(self, content: Dict, context: Dict,
                                     classification_context: str, feedback_context: str) -> str:
        """Build comprehensive classification prompt"""

        # Load prompt template from file
        with open('prompts/classification_prompt.txt', 'r', encoding='utf-8') as f:
            prompt_template = f.read()

        # Replace placeholders
        prompt = prompt_template.format(
            classification_context=classification_context,
            feedback_context=feedback_context if feedback_context else "No previous feedback available.",
            timestamp=datetime.now().isoformat()
        )

        return prompt

    def _parse_classification_response(self, response: str, message_id: str) -> ClassificationResult:
        """Parse AI response into structured classification result"""
        try:
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result_dict = json.loads(json_match.group())
            else:
                # Fallback parsing if JSON is not well-formed
                result_dict = self._extract_classification_from_text(response)

            # Validate and create ClassificationResult
            classification = ClassificationResult(
                message_id=message_id,
                category=AlertCategory(result_dict.get("category", "unknown")),
                confidence_score=float(result_dict.get("confidence_score", 0.5)),
                severity=AlertSeverity(result_dict.get("severity", "medium")),
                reasoning_chain=result_dict.get("reasoning_chain", []),
                root_cause_analysis=result_dict.get("root_cause_analysis", ""),
                suggested_actions=result_dict.get("suggested_actions", []),
                risk_assessment=result_dict.get("risk_assessment", "")
            )

            return classification

        except Exception as e:
            self.logger.error(f"Failed to parse classification response: {e}")
            return self._create_fallback_classification_result(message_id)

    def _extract_classification_from_text(self, text: str) -> Dict[str, Any]:
        """Fallback text parsing when JSON parsing fails"""
        result = {}

        # Extract category
        category_match = re.search(r'category["\s]*:["\s]*([^"]+)', text, re.IGNORECASE)
        if category_match:
            result["category"] = category_match.group(1).strip()

        # Extract confidence
        confidence_match = re.search(r'confidence["\s]*:["\s]*([0-9.]+)', text, re.IGNORECASE)
        if confidence_match:
            result["confidence_score"] = float(confidence_match.group(1))

        # Extract severity
        severity_match = re.search(r'severity["\s]*:["\s]*([^"]+)', text, re.IGNORECASE)
        if severity_match:
            result["severity"] = severity_match.group(1).strip()

        return result

    def _create_fallback_classification(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Create fallback classification when AI processing fails"""
        content = message.get("content", {})

        # Simple rule-based fallback
        category = AlertCategory.UNKNOWN
        severity = AlertSeverity.MEDIUM

        if content.get("type") == "alert":
            alert_name = content.get("alert_name", "").lower()
            if "cpu" in alert_name:
                category = AlertCategory.CPU_HIGH_USAGE
            elif "memory" in alert_name:
                category = AlertCategory.MEMORY_HIGH_USAGE
            elif "disk" in alert_name:
                category = AlertCategory.DISK_ERROR
            elif "down" in alert_name:
                category = AlertCategory.SERVICE_DOWN
                severity = AlertSeverity.CRITICAL

        elif content.get("type") == "log":
            log_level = content.get("log_level", "").lower()
            if log_level == "error":
                severity = AlertSeverity.HIGH
            elif log_level == "critical":
                severity = AlertSeverity.CRITICAL

        fallback_result = ClassificationResult(
            message_id=message.get("id", str(uuid.uuid4())),
            category=category,
            confidence_score=0.3,  # Low confidence for fallback
            severity=severity,
            reasoning_chain=["Fallback classification due to AI processing failure"],
            root_cause_analysis="Unable to perform detailed analysis - requires manual review",
            suggested_actions=["Manual investigation required", "Check system logs", "Escalate if critical"],
            risk_assessment="Unknown risk - requires immediate manual assessment"
        )

        return fallback_result.dict()

    def _create_fallback_classification_result(self, message_id: str) -> ClassificationResult:
        """Create minimal fallback classification result"""
        return ClassificationResult(
            message_id=message_id,
            category=AlertCategory.UNKNOWN,
            confidence_score=0.1,
            severity=AlertSeverity.MEDIUM,
            reasoning_chain=["Classification parsing failed"],
            root_cause_analysis="Unable to determine root cause",
            suggested_actions=["Manual review required"],
            risk_assessment="Unknown risk level"
        )

    def _store_classification_pattern(self, content: Dict[str, Any], classification: ClassificationResult):
        """Store successful classification patterns for future reference"""
        pattern_key = self._generate_pattern_key(content)

        pattern_data = {
            "content_type": content.get("type"),
            "classification": classification.dict(),
            "timestamp": datetime.utcnow().isoformat(),
            "success_count": self.classification_patterns.get(pattern_key, {}).get("success_count", 0) + 1
        }

        self.classification_patterns[pattern_key] = pattern_data

    def _generate_pattern_key(self, content: Dict[str, Any]) -> str:
        """Generate key for pattern matching"""
        if content.get("type") == "alert":
            return f"alert_{content.get('alert_name', 'unknown')}"
        elif content.get("type") == "log":
            return f"log_{content.get('service', 'unknown')}_{content.get('log_level', 'info')}"
        return "unknown"

    async def _process_feedback(self, feedback_message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process feedback from EvaluatorAgent"""

        try:
            feedback = FeedbackMessage(**feedback_message)

            # Store feedback in memory
            self.feedback_memory[feedback.original_message_id] = feedback_message

            # Get original message context (would need to be stored previously)
            original_context = self._get_original_message_context(feedback.original_message_id)

            if not original_context:
                self.logger.warning(f"No original context found for feedback on message {feedback.original_message_id}")
                return None

            # Build feedback-informed prompt
            feedback_prompt = self._build_feedback_prompt(original_context, feedback)

            # Re-classify with feedback
            system_prompt = self._get_classification_system_prompt()
            response = await self.call_lm_studio(feedback_prompt, system_prompt, CLASSIFIER_ENDPOINT, temperature=0.05)

            # Parse improved classification
            improved_classification = self._parse_classification_response(response, feedback.original_message_id)

            # Add feedback loop metadata
            result = improved_classification.dict()
            result["feedback_applied"] = True
            result["loop_count"] = feedback.loop_count

            return result

        except Exception as e:
            self.logger.error(f"Feedback processing failed: {e}")
            return None

    def _get_original_message_context(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve original message context - implement based on your storage strategy"""
        # This would typically retrieve from Redis, database, or in-memory cache
        # For now, return None - implement based on your persistence needs
        return None

    def _build_feedback_prompt(self, original_context: Dict[str, Any], feedback: FeedbackMessage) -> str:
        """Build prompt incorporating feedback"""

        with open('prompts/feedback.txt', 'r', encoding='utf-8') as f:
            prompt_template = f.read()

        # Prepare feedback details
        classification_feedback = []
        reasoning_feedback = []
        severity_feedback = []
        action_feedback = []

        if feedback.was_misclassified:
            classification_feedback.append(f"Correct category should be: {feedback.correct_category}")

        if feedback.correct_severity:
            severity_feedback.append(f"Correct severity should be: {feedback.correct_severity}")

        for issue in feedback.reasoning_issues:
            reasoning_feedback.append(issue)

        # Build the prompt
        prompt = prompt_template.format(
            original_incident=json.dumps(original_context, indent=2),
            previous_category=original_context.get("previous_category", "unknown"),
            previous_severity=original_context.get("previous_severity", "unknown"),
            previous_confidence=original_context.get("previous_confidence", 0.0),
            classification_feedback="; ".join(classification_feedback) or "No classification issues",
            reasoning_feedback="; ".join(reasoning_feedback) or "No reasoning issues",
            severity_feedback="; ".join(severity_feedback) or "No severity issues",
            action_feedback="; ".join(action_feedback) or "No action issues",
            specific_corrections=feedback.correct_category or "No specific corrections",
            improvement_suggestions="; ".join(feedback.improvement_suggestions) or "No specific suggestions",
            loop_count=feedback.loop_count
        )

        return prompt

