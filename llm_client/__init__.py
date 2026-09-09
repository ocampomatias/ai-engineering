"""Unified Async LLM Client — capa de abstracción asíncrona sobre OpenAI y Anthropic.

Pre-entrega 1 del curso AI Engineering (Coderhouse).
"""

from .base import BaseLLMClient
from .manager import AsyncLLMManager
from .providers import AnthropicClient, OpenAIClient
from .schemas import (
    ChatMessage,
    ErrorInfo,
    ModelConfig,
    ModelResponse,
    Provider,
    Role,
    StreamChunk,
    TokenUsage,
)
from .settings import Settings, load_settings

__all__ = [
    "AnthropicClient",
    "AsyncLLMManager",
    "BaseLLMClient",
    "ChatMessage",
    "ErrorInfo",
    "ModelConfig",
    "ModelResponse",
    "OpenAIClient",
    "Provider",
    "Role",
    "Settings",
    "StreamChunk",
    "TokenUsage",
    "load_settings",
]

__version__ = "1.0.0"
