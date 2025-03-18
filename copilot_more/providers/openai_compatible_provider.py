"""OpenAI compatible provider implementation."""

from typing import Dict, Optional, Any, AsyncGenerator, List, Union
import json
import asyncio
import time

from aiohttp import ClientSession, ClientTimeout
from aiohttp.client_exceptions import ClientConnectionError, ClientPayloadError

from copilot_more.providers.base_provider import BaseProvider
from copilot_more.config import request_timeout
from copilot_more.logger import logger
from copilot_more.api_key_manager import api_key_manager

class OpenAICompatibleProvider(BaseProvider):
    """Provider for OpenAI compatible APIs."""
    
    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.model_mapping = config.get("model_mapping", {})
        self.timeout = ClientTimeout(total=request_timeout)
        self.is_rate_limited = False
        self.rate_limited_until = 0
        
    async def is_available(self) -> bool:
        """Check if provider is available (not rate limited)."""
        if self.is_rate_limited and time.time() < self.rate_limited_until:
            return False
        return bool(self.api_key)
        
    async def get_token_for_request(self) -> Dict[str, str]:
        """Get API key for request."""
        return {"api_key": self.api_key}
        
    async def make_request(self, 
                          request_body: Dict[str, Any], 
                          endpoint: Optional[str] = None, 
                          accept_header: str = "application/json", 
                          stream: bool = False,
                          api_key: Optional[str] = None) -> Optional[Union[AsyncGenerator, Dict[str, Any]]]:
        """Make a request to the OpenAI compatible API."""
        # Map the model if needed
        if "model" in request_body and request_body["model"] in self.model_mapping:
            request_body = request_body.copy()
            request_body["model"] = self.model_mapping[request_body["model"]]
            
        # Determine the full endpoint URL
        if not endpoint:
            endpoint = f"{self.base_url}/chat/completions"
        elif not endpoint.startswith("http"):
            endpoint = f"{self.base_url}/{endpoint.lstrip('/')}"
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": accept_header
        }
        
        logger.debug(f"Creating new session for {self.name} request")
        async with ClientSession(timeout=self.timeout) as session:
            logger.info(f"Making API request to {endpoint}")
            
            try:
                async with session.post(
                    endpoint,
                    json=request_body,
                    headers=headers
                ) as response:
                    if response.status == 429:
                        error_message = await response.text()
                        logger.warning(f"Rate limit hit: {error_message}")
                        self.handle_rate_limit()
                        return None
                    elif response.status != 200:
                        error_message = await response.text()
                        logger.error(f"API error: {error_message}")
                        if "rate" in error_message.lower():
                            logger.warning(f"Rate limit detected in error: {error_message}")
                            self.handle_rate_limit()
                        return None
                        
                    if stream:
                        # Return a streaming generator similar to GitHub Copilot
                        async def response_generator():
                            try:
                                total_tokens = 0
                                async for chunk in response.content.iter_any():
                                    # Token counting similar to GitHub Copilot implementation
                                    try:
                                        if b'"content":' in chunk:
                                            chunk_str = chunk.decode('utf-8')
                                            if '"content"' in chunk_str:
                                                content_start = chunk_str.find('"content"') + 11
                                                content_end = chunk_str.find('",', content_start)
                                                if content_end > content_start:
                                                    content = chunk_str[content_start:content_end]
                                                    total_tokens += len(content) // 4
                                    except Exception as e:
                                        logger.error(f"Error counting tokens in chunk: {str(e)}")
                                    
                                    yield chunk
                                    await asyncio.sleep(0)
                                
                                # We only count tokens, server handles deduction
                                logger.info(f"Streaming response used approximately {total_tokens} tokens")
                                
                                # Ensure proper completion
                                if not chunk.endswith(b"data: [DONE]\n\n"):
                                    yield b"data: [DONE]\n\n"
                                    
                            except (ClientConnectionError, ClientPayloadError) as e:
                                logger.warning(f"Connection error during streaming: {str(e)}")
                                error_data = json.dumps({
                                    "error": {
                                        "message": "Connection interrupted",
                                        "type": "connection_error",
                                        "code": 503
                                    }
                                })
                                yield f"data: {error_data}\n\n".encode("utf-8")
                                yield b"data: [DONE]\n\n"
                                
                            except Exception as e:
                                logger.error(f"Streaming error: {str(e)}")
                                error_data = json.dumps({
                                    "error": {
                                        "message": f"Stream error: {str(e)}",
                                        "type": "stream_error",
                                        "code": 500
                                    }
                                })
                                yield f"data: {error_data}\n\n".encode("utf-8")
                                yield b"data: [DONE]\n\n"
                                
                        return response_generator()
                    else:
                        # Return the full response as JSON
                        resp_text = await response.json()
                        
                        # Count tokens in the response for non-streaming requests
                        total_tokens = 0
                        if "choices" in resp_text:
                            for choice in resp_text["choices"]:
                                if "message" in choice and "content" in choice["message"]:
                                    # Rough estimation: 4 chars = 1 token
                                    total_tokens += len(choice["message"]["content"]) // 4
                        
                        # Log token usage but don't deduct (server handles that)
                        logger.info(f"Non-streaming response used approximately {total_tokens} tokens")
                        
                        logger.info("Successfully completed request")
                        return resp_text
            except Exception as e:
                self.handle_failure(e)
                return None
            
    def handle_failure(self, error: Exception) -> None:
        """Handle failure in request."""
        logger.error(f"{self.name} request failed: {str(error)}")
        
    def handle_rate_limit(self) -> None:
        """Handle rate limit from OpenAI compatible API."""
        self.is_rate_limited = True
        self.rate_limited_until = time.time() + 60  # 1 minute rate limit by default
        logger.warning(f"{self.name} provider rate limited until {self.rate_limited_until}")
        
    @property
    def name(self) -> str:
        """Get the name of the provider."""
        return "openai-compatible"
