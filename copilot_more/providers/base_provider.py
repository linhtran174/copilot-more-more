"""Base provider interface for OpenAI compatible APIs."""

from abc import ABC, abstractmethod
from typing import Dict, Optional, Any, AsyncGenerator, Union

class BaseProvider(ABC):
    """Base class for all providers."""
    
    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the provider is available and can handle requests."""
        pass
    
    @abstractmethod
    async def get_token_for_request(self) -> Dict[str, str]:
        """Get authentication token for request."""
        pass
    
    @abstractmethod
    async def make_request(self, 
                          request_body: Dict[str, Any], 
                          endpoint: Optional[str] = None, 
                          accept_header: str = "application/json", 
                          stream: bool = False,
                          api_key: Optional[str] = None) -> Optional[Union[AsyncGenerator, Dict[str, Any]]]:
        """Make a request to the provider's API endpoint.
        
        Args:
            request_body: The request body to send to the API
            endpoint: Optional specific endpoint to use (default is chat/completions)
            accept_header: Accept header to use for the request
            stream: Whether to stream the response
            api_key: API key for token accounting
            
        Returns:
            If stream=True, returns an async generator that yields chunks of the response
            If stream=False, returns the response as a dict
            Returns None if the request fails
        """
        pass
    
    @abstractmethod
    def handle_failure(self, error: Exception) -> None:
        """Handle failure in request."""
        pass
    
    @abstractmethod
    def handle_rate_limit(self) -> None:
        """Handle rate limit response from provider."""
        pass
        
    @property
    @abstractmethod
    def name(self) -> str:
        """Get the name of the provider."""
        pass