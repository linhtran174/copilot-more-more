"""Provider manager for OpenAI compatible APIs."""

from typing import Dict, List, Optional, Any, AsyncGenerator, Union
import time
import asyncio

from copilot_more.providers.base_provider import BaseProvider
from copilot_more.providers.github_copilot_provider import GithubCopilotProvider
from copilot_more.providers.openai_compatible_provider import OpenAICompatibleProvider
from copilot_more.logger import logger
from copilot_more.config import GithubCopilotProviderConfig, OpenAICompatibleProviderConfig

class ProviderManager:
    """Manages multiple AI providers."""
    
    def __init__(self):
        self.providers: List[BaseProvider] = []
        self.priority_order: List[int] = []  # Index of providers in priority order
        self.current_provider = None
        
    def initialize_providers(self, provider_configs: List[Dict[str, Any]]) -> None:
        """Initialize providers from configuration.
        
        Args:
            provider_configs: List of provider configurations from config.json
        """
        self.providers = []
        self.priority_order = []
        
        # Sort provider configs by priority
        sorted_configs = sorted(provider_configs, key=lambda x: x.get("priority", 999))
        
        for config in sorted_configs:
            if not config.get("enabled", True):
                continue
                
            provider_type = config.get("type", "").lower()
            if provider_type == "github-copilot":
                provider_config = GithubCopilotProviderConfig(
                    type=provider_type,
                    enabled=config.get("enabled", True),
                    priority=config.get("priority", 1),
                    rate_limit_windows=config.get("rate_limit_windows", []),
                    accounts=config.get("accounts", [])
                )
                provider = GithubCopilotProvider(provider_config)
                self.providers.append(provider)
                self.priority_order.append(len(self.providers) - 1)
                logger.info("Initialized GitHub Copilot provider")
            elif provider_type == "openai-compatible":
                provider = OpenAICompatibleProvider(config)
                self.providers.append(provider)
                self.priority_order.append(len(self.providers) - 1)
                logger.info(f"Initialized OpenAI compatible provider with base URL: {config.get('base_url')}")
        
        logger.info(f"Initialized {len(self.providers)} providers in priority order")
        
    async def get_next_available_provider(self) -> Optional[BaseProvider]:
        """Get the next available provider in priority order.
        
        Returns:
            The next available provider, or None if no providers are available
        """
        for idx in self.priority_order:
            provider = self.providers[idx]
            if await provider.is_available():
                self.current_provider = provider
                return provider
        return None
        
    def handle_provider_failure(self, provider: BaseProvider) -> None:
        """Handle provider failure by logging it.
        
        Args:
            provider: The provider that failed
        """
        logger.warning(f"Provider {provider.name} failed")
        
    async def make_request(self, 
                          request_body: Dict[str, Any], 
                          endpoint: Optional[str] = None, 
                          accept_header: str = "application/json", 
                          stream: bool = False,
                          api_key: Optional[str] = None) -> Optional[Union[AsyncGenerator, Dict[str, Any]]]:
        """Make a request with automatic provider failover.
        
        This method will try each available provider in priority order until one succeeds.
        
        Args:
            request_body: The request body to send
            endpoint: Optional endpoint to use
            accept_header: The Accept header to use
            stream: Whether to stream the response
            api_key: The API key to use for token accounting
            
        Returns:
            The response from the first provider that succeeds, or None if all providers fail
        """
        # Try providers in priority order
        for idx in self.priority_order:
            provider = self.providers[idx]
            
            if not await provider.is_available():
                logger.info(f"Provider {provider.name} is not available, trying next")
                continue
                
            logger.info(f"Trying provider: {provider.name}")
            try:
                # Get provider token
                token_info = await provider.get_token_for_request()
                if not token_info:
                    logger.warning(f"Provider {provider.name} failed to provide authentication token")
                    continue
                
                # Make the request
                result = await provider.make_request(
                    request_body=request_body,
                    endpoint=endpoint,
                    accept_header=accept_header,
                    stream=stream,
                    api_key=api_key
                )
                
                if result is not None:
                    logger.info(f"Successfully made request with provider {provider.name}")
                    return result
                else:
                    logger.warning(f"Provider {provider.name} returned None, trying next")
            except Exception as e:
                logger.error(f"Error with provider {provider.name}: {str(e)}")
                provider.handle_failure(e)
                
        logger.error("All providers failed, unable to complete request")
        return None

# Global provider manager instance
provider_manager = ProviderManager()