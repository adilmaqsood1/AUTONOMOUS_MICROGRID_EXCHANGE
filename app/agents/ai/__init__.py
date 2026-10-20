"""Phase 4 AI agent intelligence package."""
from app.agents.ai.advisors import GridAIAgent, MarketAnalystAgent
from app.agents.ai.attribution import attribute_agent_outcome
from app.agents.ai.context import AgentReasoningContext
from app.agents.ai.guard import AgentPolicyGuard
from app.agents.ai.memory import AgentMemory, load_agent_memory, save_agent_memory
from app.agents.ai.parser import parse_and_validate
from app.agents.ai.prompts import build_agent_prompt
from app.agents.ai.provider import (
    AIReasoningProvider,
    MockAIReasoningProvider,
    OpenAICompatibleReasoningProvider,
    get_ai_provider,
    set_ai_provider,
)
from app.agents.ai.rewards import (
    compute_battery_reward,
    compute_commercial_reward,
    compute_consumer_reward,
    compute_ev_reward,
    compute_generator_reward,
    compute_prosumer_reward,
)
from app.agents.ai.schemas import (
    AgentStrategyProposal,
    GridAdvisorProposal,
    GuardResult,
    MarketExplanation,
)
from app.agents.ai.strategies import HybridAgentRunner

__all__ = [
    "AgentReasoningContext",
    "AgentStrategyProposal",
    "GridAdvisorProposal",
    "MarketExplanation",
    "GuardResult",
    "AgentPolicyGuard",
    "AIReasoningProvider",
    "MockAIReasoningProvider",
    "OpenAICompatibleReasoningProvider",
    "get_ai_provider",
    "set_ai_provider",
    "GridAIAgent",
    "MarketAnalystAgent",
    "HybridAgentRunner",
    "AgentMemory",
    "load_agent_memory",
    "save_agent_memory",
    "attribute_agent_outcome",
    "build_agent_prompt",
    "parse_and_validate",
    "compute_generator_reward",
    "compute_consumer_reward",
    "compute_prosumer_reward",
    "compute_battery_reward",
    "compute_ev_reward",
    "compute_commercial_reward",
]
