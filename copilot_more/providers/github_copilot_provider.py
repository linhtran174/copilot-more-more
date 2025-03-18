"""GitHub Copilot provider implementation."""

from typing import Dict, Optional, Any, AsyncGenerator, List, Union
import json
import asyncio
import logging

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from aiohttp_socks import ProxyConnector

from copilot_more.providers.base_provider import BaseProvider
from copilot_more.account_manager import AccountManager
from copilot_more.config import request_timeout, GithubCopilotProviderConfig
# from copilot_more.proxy import RECORD_TRAFFIC, get_proxy_url
from copilot_more.logger import logger
from copilot_more.api_key_manager import api_key_manager

class GithubCopilotProvider(BaseProvider):
    """Provider implementation for GitHub Copilot."""
    
    def __init__(self, provider_config: GithubCopilotProviderConfig):
        """Initialize provider with configuration."""
        self.account_manager = AccountManager.create_from_config(provider_config.accounts)
        self.chat_completions_endpoint = "https://api.individual.githubcopilot.com/chat/completions"
        self.models_endpoint = "https://api.individual.githubcopilot.com/models"
        self.timeout = ClientTimeout(total=request_timeout)
        self.current_account = None
        
    async def is_available(self) -> bool:
        """Check if there's at least one usable account."""
        return self.account_manager.has_usable_accounts()
    
    async def get_token_for_request(self) -> Dict[str, str]:
        """Get access token for request."""
        self.current_account = self.account_manager.get_next_usable_account()
        if not self.current_account:
            logger.error("No usable GitHub Copilot account available")
            return {}
            
        token = await self.current_account.get_access_token()
        if not token:
            logger.error(f"Failed to get access token for account {self.current_account.username}")
            return {}
            
        # Record this request for rate limiting
        self.current_account.record_request()
        return token
    
    async def make_request(self, 
                          request_body: Dict[str, Any], 
                          endpoint: Optional[str] = None, 
                          accept_header: str = "application/json", 
                          stream: bool = False,
                          api_key: Optional[str] = None) -> Optional[Union[AsyncGenerator, Dict[str, Any]]]:
        """Make a request to the GitHub Copilot API."""
        if not self.current_account:
            logger.error("No account selected for request")
            return None
            
        if not endpoint:
            endpoint = self.chat_completions_endpoint
            
        token = await self.current_account.get_access_token()
        if not token:
            logger.error(f"Failed to get access token for account {self.current_account.username}")
            return None
            
        headers = {
            "Authorization": f"Bearer {token['token']}",
            "Content-Type": "application/json",
            "Accept": accept_header,
            "editor-version": "vscode/1.95.3",
            "editor-plugin-version": "github.copilot/1.277.0",
            "user-agent": "GithubCopilot/1.155.0",
        }
        
        # proxy = get_proxy_url() if RECORD_TRAFFIC else None
        connector = self.current_account.get_proxy_connector()
        
        logger.debug("Creating new session for GitHub Copilot request")
        async with ClientSession(
            timeout=self.timeout,
            connector=connector if connector else None
        ) as session:
            logger.info(f"Making API request to {endpoint}")
            if session.closed:
                logger.error("Session is closed")
                return None
                
            async with session.post(
                endpoint,
                json=request_body if request_body else {},
                headers=headers,
                # proxy=proxy
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
                    # Return a streaming generator that preserves SSE format
                    logger.info("Creating streaming response generator")
                    
                    async def response_generator():
                        """Generate streaming response with proper SSE handling."""
                        from aiohttp.client_exceptions import ClientConnectionError, ClientPayloadError
                        
                        try:
                            total_tokens = 0
                            async for chunk in response.content.iter_any():
                                # Track tokens for each chunk that contains content
                                try:
                                    if b'"content":' in chunk:
                                        # Parse the chunk to get content length
                                        chunk_str = chunk.decode('utf-8')
                                        if '"content"' in chunk_str:
                                            content_start = chunk_str.find('"content"') + 11
                                            content_end = chunk_str.find('",', content_start)
                                            if content_end > content_start:
                                                content = chunk_str[content_start:content_end]
                                                # Rough estimation: 4 chars = 1 token
                                                total_tokens += len(content) // 4
                                except Exception as e:
                                    logger.error(f"Error counting tokens in chunk: {str(e)}")
                                
                                # Preserve original SSE formatting by yielding raw chunks
                                yield chunk
                                await asyncio.sleep(0)  # Yield control to event loop
                            
                            # Update token usage at the end of stream
                            if api_key:
                                try:
                                    api_key_manager.deduct_tokens(api_key, total_tokens)
                                    logger.info(f"Deducted {total_tokens} tokens from API key")
                                except Exception as e:
                                    logger.error(f"Error deducting tokens: {str(e)}")
                            
                            # Ensure final completion marker if not present
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
                    
                    # Update token usage
                    if api_key:
                        try:
                            api_key_manager.deduct_tokens(api_key, total_tokens)
                            logger.info(f"Deducted {total_tokens} tokens from API key")
                        except Exception as e:
                            logger.error(f"Error deducting tokens: {str(e)}")
                    
                    logger.info("Successfully completed request")
                    return resp_text

    def handle_failure(self, error: Exception) -> None:
        """Handle failure in request."""
        logger.error(f"GitHub Copilot request failed: {str(error)}")
        
    def handle_rate_limit(self) -> None:
        """Handle rate limit from GitHub Copilot."""
        if self.current_account:
            self.current_account.mark_rate_limited()
            logger.warning(f"Account {self.current_account.username} rate limited")
            
    @property
    def name(self) -> str:
        """Get the name of the provider."""
        return "github-copilot"