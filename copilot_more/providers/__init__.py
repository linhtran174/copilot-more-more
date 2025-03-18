"""Provider abstraction for OpenAI compatible APIs."""

from copilot_more.providers.base_provider import BaseProvider

__all__ = ['BaseProvider', 'provider_manager']

# Import provider_manager after BaseProvider to avoid circular imports
from copilot_more.providers.provider_manager import provider_manager