# run_agents.py
import asyncio
import logging
from ai_core.orchestrator_agent import OrchestratorAgent
from ai_core.classifier_agent import ClassifierReasonerAgent
from ai_core.evaluator_agent import EvaluatorAgent

async def main():
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("MCP")

    # Initialize agents
    orchestrator = OrchestratorAgent()
    classifier = ClassifierReasonerAgent()
    evaluator = EvaluatorAgent()

    try:
        # Start all agents
        agent_tasks = [
            asyncio.create_task(orchestrator.start()),
            asyncio.create_task(classifier.start()),
            asyncio.create_task(evaluator.start())
        ]

        logger.info("🚀 Starting MCP System")
        await asyncio.gather(*agent_tasks)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        # Stop agents
        await orchestrator.stop()
        await classifier.stop()
        await evaluator.stop()

if __name__ == "__main__":
    asyncio.run(main())