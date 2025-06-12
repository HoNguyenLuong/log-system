# mcp_schema.py
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class AlertCategory(str, Enum):
    CPU_HIGH_USAGE = "cpu_high_usage"
    MEMORY_HIGH_USAGE = "memory_high_usage"
    DISK_ERROR = "disk_error"
    NETWORK_ERROR = "network_error"
    AUTH_FAILURE = "auth_failure"
    SERVICE_DOWN = "service_down"
    DATABASE_ERROR = "database_error"
    SECURITY_INCIDENT = "security_incident"
    UNKNOWN = "unknown"

class MCPMessage(BaseModel):
    """Base MCP Message Format"""
    id: str = Field(..., description="Unique message ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = Field(..., description="prometheus/elasticsearch")
    agent_route: str = Field(..., description="Target agent")
    content: Dict[str, Any] = Field(..., description="Message content")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Additional context")

class ClassificationResult(BaseModel):
    """Classification Output"""
    message_id: str
    category: AlertCategory
    confidence_score: float = Field(ge=0.0, le=1.0)
    severity: AlertSeverity
    reasoning_chain: List[str]
    root_cause_analysis: str
    suggested_actions: List[str]
    risk_assessment: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class EvaluationResult(BaseModel):
    """Evaluation Output"""
    message_id: str
    classification_accuracy: float = Field(ge=0.0, le=1.0)
    reasoning_quality: float = Field(ge=0.0, le=1.0)
    severity_accuracy: float = Field(ge=0.0, le=1.0)
    overall_score: float = Field(ge=0.0, le=1.0)
    final_category: AlertCategory
    final_severity: AlertSeverity
    final_actions: List[str]
    needs_feedback: bool = False
    is_final: bool = False
    loop_count: int = 0

class FeedbackMessage(BaseModel):
    """A2A Feedback Message"""
    original_message_id: str
    target_agent: str = "classifier_reasoner"
    was_misclassified: bool
    correct_category: Optional[AlertCategory] = None
    correct_severity: Optional[AlertSeverity] = None
    reasoning_issues: List[str] = []
    improvement_suggestions: List[str] = []
    loop_count: int
    feedback_type: str = "correction"  # correction, enhancement, validation