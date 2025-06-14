# evaluator_agent.py
import json
import re
from typing import Dict, Any, List, Optional
from datetime import datetime
from base_agent import BaseAgent
from mcp_schema import EvaluationResult, FeedbackMessage, AlertCategory, AlertSeverity

EVALUATOR_ENDPOINT = "http://127.0.0.1:5000/v1/chat/completions"


class EvaluatorAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(
            agent_name="evaluator",
            # model_name="qwen2.5:14b-instruct",
            model_name="qwen2.5:14b-instruct",
            **kwargs
        )
        self.evaluation_history = {}
        self.feedback_templates = {}

    def get_input_topics(self) -> List[str]:
        return ["evaluator_in"]

    def get_output_topics(self) -> Dict[str, str]:
        return {
            "default": "evaluated_alert",
            "feedback": "classifier_feedback"
        }

    def _determine_output_topic(self, result: Dict[str, Any], output_topics: Dict[str, str]) -> Optional[str]:
        """Determine output topic based on evaluation result"""
        if result.get("needs_feedback"):
            return output_topics["feedback"]
        return output_topics["default"]

    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Evaluate classification result and provide feedback if needed"""

        try:
            message_id = message.get("message_id")

            # Build evaluation context
            evaluation_context = self._prepare_evaluation_context(message)

            # Get evaluation from AI
            evaluation_prompt = self._build_evaluation_prompt(message, evaluation_context)
            system_prompt = self._get_evaluation_system_prompt()

            response = await self.call_lm_studio(evaluation_prompt, system_prompt, EVALUATOR_ENDPOINT, temperature=0.05)

            # Parse evaluation result
            evaluation = self._parse_evaluation_response(response, message_id)

            # Determine if feedback is needed
            if evaluation.needs_feedback and evaluation.loop_count < 3:
                return self._create_feedback_message(message, evaluation)
            else:
                # Create final evaluation result
                evaluation.is_final = True
                return evaluation.dict()

        except Exception as e:
            self.logger.error(f"Evaluation failed: {e}")
            return self._create_fallback_evaluation(message)

    def _get_evaluation_system_prompt(self) -> str:
        """Load evaluation system prompt"""
        with open('prompts/evaluation_system.txt', 'r', encoding='utf-8') as f:
            return f.read()

    def _prepare_evaluation_context(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare context for evaluation"""

        # Extract classification details
        context = {
            "classified_category": message.get("category", "unknown"),
            "confidence_score": message.get("confidence_score", 0.0),
            "classified_severity": message.get("severity", "medium"),
            "reasoning_chain": message.get("reasoning_chain", []),
            "root_cause_analysis": message.get("root_cause_analysis", ""),
            "suggested_actions": message.get("suggested_actions", []),
            "risk_assessment": message.get("risk_assessment", "")
        }

        # Add historical context if available
        message_id = message.get("message_id", "")
        if message_id in self.evaluation_history:
            context["previous_evaluations"] = self.evaluation_history[message_id]

        return context 

    def _build_evaluation_prompt(self, message: Dict[str, Any], context: Dict[str, Any]) -> str:
        """Build evaluation prompt"""

        with open('prompts/evaluation_prompt.txt', 'r', encoding='utf-8') as f:
            prompt_template = f.read()

        # Get loop count for feedback context
        loop_count = message.get("loop_count", 0)
        previous_feedback = self._get_previous_feedback_summary(message.get("message_id", ""))

        prompt = prompt_template.format(
            original_incident=json.dumps(message.get("original_incident", {}), indent=2),
            classified_category=context["classified_category"],
            confidence_score=context["confidence_score"],
            classified_severity=context["classified_severity"],
            reasoning_chain=json.dumps(context["reasoning_chain"], indent=2),
            root_cause_analysis=context["root_cause_analysis"],
            suggested_actions=json.dumps(context["suggested_actions"], indent=2),
            risk_assessment=context["risk_assessment"],
            loop_count=loop_count,
            previous_feedback=previous_feedback,
            timestamp=datetime.now().isoformat()
        )

        return prompt

    def _get_previous_feedback_summary(self, message_id: str) -> str:
        """Get summary of previous feedback for this message"""
        if message_id in self.evaluation_history:
            history = self.evaluation_history[message_id]
            return f"Previous feedback provided {len(history)} times"
        return "No previous feedback"

    def _parse_evaluation_response(self, response: str, message_id: str) -> EvaluationResult:
        """Parse AI evaluation response"""

        try:
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result_dict = json.loads(json_match.group())
            else:
                result_dict = self._extract_evaluation_from_text(response)

            # Create EvaluationResult
            evaluation = EvaluationResult(
                message_id=message_id,
                accuracy_score=float(result_dict.get("accuracy_score", 0.5)),
                is_accurate=result_dict.get("is_accurate", True),
                needs_feedback=result_dict.get("needs_feedback", False),
                severity_correct=result_dict.get("severity_correct", True),
                risk_level=result_dict.get("risk_level", "medium"),
                confidence_assessment=result_dict.get("confidence_assessment", ""),
                improvement_suggestions=result_dict.get("improvement_suggestions", []),
                loop_count=result_dict.get("loop_count", 0),
                is_final=result_dict.get("is_final", False)
            )

            return evaluation

        except Exception as e:
            self.logger.error(f"Failed to parse evaluation response: {e}")
            return self._create_fallback_evaluation_result(message_id)

    def _extract_evaluation_from_text(self, text: str) -> Dict[str, Any]:
        """Extract evaluation data from text when JSON parsing fails"""
        result = {
            "accuracy_score": 0.5,
            "is_accurate": True,
            "needs_feedback": False,
            "severity_correct": True,
            "risk_level": "medium",
            "confidence_assessment": "",
            "improvement_suggestions": [],
            "loop_count": 0,
            "is_final": False
        }

        # Extract accuracy score
        accuracy_match = re.search(r'accuracy["\s]*:["\s]*([0-9.]+)', text, re.IGNORECASE)
        if accuracy_match:
            result["accuracy_score"] = float(accuracy_match.group(1))

        # Extract needs_feedback
        if re.search(r'needs?\s*feedback:\s*true', text, re.IGNORECASE):
            result["needs_feedback"] = True

        # Extract risk level
        risk_match = re.search(r'risk["\s]*:["\s]*([^"]+)', text, re.IGNORECASE)
        if risk_match:
            result["risk_level"] = risk_match.group(1).strip()

        return result

    def _create_feedback_message(self, original_message: Dict[str, Any], 
                               evaluation: EvaluationResult) -> Dict[str, Any]:
        """Create feedback message for classifier"""
        
        feedback = FeedbackMessage(
            original_message_id=original_message.get("message_id", ""),
            was_misclassified=not evaluation.is_accurate,
            correct_category=self._determine_correct_category(original_message, evaluation),
            correct_severity=None if evaluation.severity_correct else self._determine_correct_severity(evaluation),
            reasoning_issues=evaluation.improvement_suggestions,
            loop_count=evaluation.loop_count + 1
        )

        # Store feedback in history
        self._store_feedback(feedback)

        return feedback.dict()

    def _determine_correct_category(self, message: Dict[str, Any], 
                                  evaluation: EvaluationResult) -> Optional[AlertCategory]:
        """Determine correct category based on evaluation"""
        if not evaluation.is_accurate:
            # Logic to determine correct category
            content = message.get("content", {})
            if "cpu" in str(content).lower():
                return AlertCategory.CPU_HIGH_USAGE
            elif "memory" in str(content).lower():
                return AlertCategory.MEMORY_HIGH_USAGE
            elif "disk" in str(content).lower():
                return AlertCategory.DISK_ERROR
        return None

    def _determine_correct_severity(self, evaluation: EvaluationResult) -> Optional[AlertSeverity]:
        """Determine correct severity based on evaluation"""
        if evaluation.risk_level == "high":
            return AlertSeverity.HIGH
        elif evaluation.risk_level == "critical":
            return AlertSeverity.CRITICAL
        return None

    def _store_feedback(self, feedback: FeedbackMessage):
        """Store feedback in evaluation history"""
        message_id = feedback.original_message_id
        if message_id not in self.evaluation_history:
            self.evaluation_history[message_id] = []
        self.evaluation_history[message_id].append(feedback.dict())

    def _create_fallback_evaluation(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Create fallback evaluation when processing fails"""
        fallback = EvaluationResult(
            message_id=message.get("message_id", ""),
            accuracy_score=0.5,
            is_accurate=True,  # Assume accurate to avoid feedback loop
            needs_feedback=False,
            severity_correct=True,
            risk_level="medium",
            confidence_assessment="Fallback evaluation due to processing error",
            improvement_suggestions=["Manual review recommended"],
            loop_count=0,
            is_final=True
        )
        return fallback.dict()

    def _create_fallback_evaluation_result(self, message_id: str) -> EvaluationResult:
        """Create minimal fallback evaluation result"""
        return EvaluationResult(
            message_id=message_id,
            accuracy_score=0.5,
            is_accurate=True,
            needs_feedback=False,
            severity_correct=True,
            risk_level="medium",
            confidence_assessment="Fallback evaluation",
            improvement_suggestions=[],
            loop_count=0,
            is_final=True
        )