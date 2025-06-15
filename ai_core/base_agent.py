# base_agent.py
import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
import httpx
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError
from kafka.admin import KafkaAdminClient, NewTopic
from config import LLM_CHAT_ENDPOINT, DEFAULT_TEMPERATURE, MAX_RETRIES, TIMEOUT

 
class BaseAgent(ABC):
    def __init__(self,
                 agent_name: str,
                 model_name: str,
                 kafka_bootstrap_servers: str = "localhost:9192"):

        self.agent_name = agent_name
        self.model_name = model_name
        self.kafka_bootstrap_servers = kafka_bootstrap_servers

        # Logging setup
        self.logger = logging.getLogger(f"Agent.{agent_name}")
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            f'%(asctime)s - {agent_name} - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

        # Kafka setup
        self.producer = KafkaProducer(
            bootstrap_servers=[kafka_bootstrap_servers],
            value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8'),
            key_serializer=lambda v: v.encode('utf-8') if v else None,
            retries=3,
            acks='all'
        )

        self.consumer = None
        self.running = False
        self.message_handlers = {}

    async def send_message(self, topic: str, message: Dict[str, Any], key: Optional[str] = None):
        """Send message to Kafka topic with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                future = self.producer.send(topic, value=message, key=key)
                record_metadata = future.get(timeout=10)
                self.logger.info(
                    f"✓ Message sent to {topic}: partition={record_metadata.partition}, offset={record_metadata.offset}")
                return True
            except KafkaError as e:
                self.logger.error(f"✗ Attempt {attempt + 1} failed to send to {topic}: {e}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
        return False

    # async def call_ollama(self, prompt: str, system_prompt: str = "", temperature: float = 0.1) -> str:
    #     """Call Ollama API for AI inference with enhanced error handling"""
    #     async with httpx.AsyncClient(timeout=300.0) as client:
    #         payload = {
    #             "model": self.model_name,
    #             "prompt": prompt,
    #             "system": system_prompt,
    #             "stream": False,
    #             "options": {
    #                 "temperature": temperature,
    #                 "top_p": 0.9,
    #                 "top_k": 40,
    #                 "repeat_penalty": 1.1
    #             }
    #         }
    #
    #         max_retries = 3
    #         for attempt in range(max_retries):
    #             try:
    #                 response = await client.post(f"{self.ollama_base_url}/api/generate", json=payload)
    #                 response.raise_for_status()
    #                 result = response.json()
    #                 return result.get("response", "").strip()
    #             except Exception as e:
    #                 self.logger.error(f"Ollama API attempt {attempt + 1} failed: {e}")
    #                 if attempt == max_retries - 1:
    #                     raise
    #                 await asyncio.sleep(2 ** attempt)

    async def call_lm_studio(self, prompt: str, system_prompt: str = "", 
                            api_url: str = LLM_CHAT_ENDPOINT, 
                            temperature: float = DEFAULT_TEMPERATURE) -> str:
        """Call LM Studio API for AI inference"""
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]

            payload = {
                "messages": messages,
                "model": self.model_name,
                "temperature": temperature,
                "stream": False
            }

            for attempt in range(MAX_RETRIES):
                try:
                    response = await client.post(api_url, json=payload)
                    response.raise_for_status()
                    result = response.json()
                    return result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                except Exception as e:
                    self.logger.error(f"LM Studio API call failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                    if attempt == MAX_RETRIES - 1:
                        return ""
                    await asyncio.sleep(2 ** attempt)
            return ""

    def register_message_handler(self, message_type: str, handler: Callable):
        """Register message handler for specific message types"""
        self.message_handlers[message_type] = handler

    @abstractmethod
    async def process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process incoming message - implemented by each agent"""
        pass

    def _create_output_topics(self):
        """Ensure output topics exist in Kafka"""
        output_topics = list(self.get_output_topics().values())

        try:
            admin_client = KafkaAdminClient(
                bootstrap_servers=self.kafka_bootstrap_servers,
                client_id=f"{self.agent_name}_admin"
            )

            existing_topics = admin_client.list_topics()
            topics_to_create = [
                NewTopic(name=topic, num_partitions=1, replication_factor=1)
                for topic in output_topics if topic not in existing_topics
            ]

            if topics_to_create:
                admin_client.create_topics(new_topics=topics_to_create, validate_only=False)
                self.logger.info(f"🧵 Created topics: {[t.name for t in topics_to_create]}")
            else:
                self.logger.info("✅ All output topics already exist")

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to create output topics: {e}")

    @abstractmethod
    def get_input_topics(self) -> List[str]:
        """Get input topics for this agent"""
        pass

    @abstractmethod
    def get_output_topics(self) -> Dict[str, str]:
        """Get output topics mapping"""
        pass

    async def start(self):
        """Start the agent with enhanced error handling"""
        self.running = True
        self._create_output_topics()
        self.consumer = KafkaConsumer(
            *self.get_input_topics(),
            bootstrap_servers=[self.kafka_bootstrap_servers],
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            group_id=f"{self.agent_name}_group",
            auto_offset_reset='latest',
            enable_auto_commit=True,
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000
        )

        self.logger.info(f"🚀 Agent {self.agent_name} started")
        self.logger.info(f"📥 Listening to topics: {self.get_input_topics()}")
        self.logger.info(f"📤 Output topics: {self.get_output_topics()}")

        while self.running:
            try:
                message_pack = self.consumer.poll(timeout_ms=1000)
                for topic_partition, messages in message_pack.items():
                    for message in messages:
                        await self._handle_message(message)

            except Exception as e:
                self.logger.error(f"Consumer error: {e}")
                await asyncio.sleep(5)

    async def _handle_message(self, kafka_message):
        """Handle individual Kafka message"""
        try:
            message_data = kafka_message.value
            message_id = message_data.get("id", str(uuid.uuid4()))

            self.logger.info(f"📨 Processing message {message_id} from {kafka_message.topic}")

            # Process message
            result = await self.process_message(message_data)

            if result:
                # Determine output topic
                output_topics = self.get_output_topics()
                target_topic = self._determine_output_topic(result, output_topics)

                if target_topic:
                    await self.send_message(target_topic, result, key=message_id)
                    self.logger.info(f"✅ Message {message_id} processed successfully")
                else:
                    self.logger.warning(f"⚠️ No output topic determined for message {message_id}")
            else:
                self.logger.info(f"ℹ️ Message {message_id} processed but no output generated")

        except Exception as e:
            self.logger.error(f"❌ Error handling message: {e}")

    def _determine_output_topic(self, result: Dict[str, Any], output_topics: Dict[str, str]) -> Optional[str]:
        """Determine output topic based on result"""
        # Default implementation - can be overridden
        return output_topics.get("default") or list(output_topics.values())[0] if output_topics else None

    async def stop(self):
        """Stop the agent gracefully"""
        self.logger.info(f"🛑 Stopping agent {self.agent_name}")
        self.running = False
        if self.consumer:
            self.consumer.close()
        self.producer.close()