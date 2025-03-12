import json
import asyncio
import traceback

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Optional

from copilot_more.account_manager import account_manager
from copilot_more.logger import logger
from copilot_more.config import request_timeout
from copilot_more.proxy import RECORD_TRAFFIC, get_proxy_url
from copilot_more.utils import StringSanitizer
from copilot_more.api_key_manager import api_key_manager
from copilot_more.api_routes import router as api_router

sanitizer = StringSanitizer()

app = FastAPI()

# Include API routes
app.include_router(api_router, prefix="/v1", tags=["api"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHAT_COMPLETIONS_API_ENDPOINT = "https://api.individual.githubcopilot.com/chat/completions"
MODELS_API_ENDPOINT = "https://api.individual.githubcopilot.com/models"
TIMEOUT = ClientTimeout(total=request_timeout)
MAX_TOKENS = 10240

def preprocess_request_body(request_body: dict) -> dict:
    """Preprocess the request body to handle array content in messages."""
    if not request_body.get("messages"):
        return request_body

    processed_messages = []
    for message in request_body["messages"]:
        if not isinstance(message.get("content"), list):
            content = message["content"]
            if isinstance(content, str):
                result = sanitizer.sanitize(content)
                if not result.success:
                    logger.warning(f"String sanitization warnings: {result.warnings}")
                content = result.text
            message["content"] = content
            processed_messages.append(message)
            continue

        for content_item in message["content"]:
            if content_item.get("type") != "text":
                raise HTTPException(400, "Only text type is supported in content array")

            text = content_item["text"]
            if isinstance(text, str):
                result = sanitizer.sanitize(text)
                if not result.success:
                    logger.warning(f"String sanitization warnings: {result.warnings}")
                text = result.text

            processed_messages.append({"role": message["role"], "content": text})

    # o1 models don't support system messages
    model: str = request_body.get("model", "")
    if model and model.startswith("o1"):
        for message in processed_messages:
            if message["role"] == "system":
                message["role"] = "user"

    max_tokens = request_body.get("max_tokens", MAX_TOKENS)
    # Set default model if not specified
    model = request_body.get("model", "gpt-4")
    return {**request_body, "model": model, "messages": processed_messages, "max_tokens": max_tokens}

async def make_request(account, request_body, api_key: str, endpoint=CHAT_COMPLETIONS_API_ENDPOINT, accept_header="text/event-stream", stream=False):
    """Make a single request with proper session handling."""
    logger.info("Fetching access token")
    token = await account.get_access_token()
    if not token:
        raise ValueError("Failed to get access token")
    
    # Record this request for rate limiting
    account.record_request()

    headers = {
        "Authorization": f"Bearer {token['token']}",
        "Content-Type": "application/json",
        "Accept": accept_header,
        "editor-version": "vscode/1.95.3",
        "editor-plugin-version": "github.copilot/1.277.0",
        "user-agent": "GithubCopilot/1.155.0",
    }
    proxy = get_proxy_url() if RECORD_TRAFFIC else None
    connector = account.get_proxy_connector()

    logger.debug("Creating new session")
    async with ClientSession(
        timeout=TIMEOUT,
        connector=connector if connector else None
    ) as session:
        logger.info(f"Making API request to {endpoint}")
        if(session.closed):
            logger.error("Session is closed")
            return None
        
        async with session.post(
            endpoint,
            json=request_body if request_body else {},
            headers=headers,
            proxy=proxy
        ) as response:
            if response.status == 429:
                error_message = await response.text()
                logger.warning(f"Rate limit hit: {error_message}")
                account.mark_rate_limited()
                
                # For streaming requests, create an SSE-compliant error message
                if stream:
                    async def error_stream():
                        try:
                            error_data = json.dumps({
                                "error": {
                                    "message": f"Rate limit exceeded. Please try again later.",
                                    "type": "rate_limit_error",
                                    "code": 429
                                }
                            })
                            yield f"data: {error_data}\n\ndata: [DONE]\n\n".encode("utf-8")
                        except Exception as e:
                            logger.error(f"Error generating rate limit error stream: {str(e)}")
                            yield b"data: [DONE]\n\n"
                    return error_stream()
                return None
            elif response.status != 200:
                error_message = await response.text()
                logger.error(f"API error: {error_message}")
                if "rate" in error_message.lower():
                    logger.warning(f"Rate limit detected in error: {error_message}")
                    account.mark_rate_limited()
                    
                    # For streaming requests, create an SSE-compliant error message
                    if stream:
                        async def error_stream():
                            try:
                                error_data = json.dumps({
                                    "error": {
                                        "message": f"Rate limit detected in error response.",
                                        "type": "rate_limit_error",
                                        "code": response.status
                                    }
                                })
                                yield f"data: {error_data}\n\ndata: [DONE]\n\n".encode("utf-8")
                            except Exception as e:
                                logger.error(f"Error generating rate limit error stream: {str(e)}")
                                yield b"data: [DONE]\n\n"
                        return error_stream()
                    return None
                
                # For streaming requests, create an SSE-compliant error message
                if stream:
                    async def error_stream():
                        try:
                            error_data = json.dumps({
                                "error": {
                                    "message": f"API error: {error_message}",
                                    "type": "api_error",
                                    "code": response.status
                                }
                            })
                            yield f"data: {error_data}\n\ndata: [DONE]\n\n".encode("utf-8")
                        except Exception as e:
                            logger.error(f"Error generating API error stream: {str(e)}")
                            yield b"data: [DONE]\n\n"
                    return error_stream()
                raise HTTPException(response.status, f"API error: {error_message}")
                
            if stream:
                # Return a streaming generator that preserves SSE format
                logger.info("Creating streaming response generator")
                async def response_generator():
                    """
                    Generate streaming response with proper SSE handling.
                    Streams chunks incrementally while maintaining SSE format.
                    """
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
                
                logger.info("Streaming response ready")
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
                try:
                    api_key_manager.deduct_tokens(api_key, total_tokens)
                    logger.info(f"Deducted {total_tokens} tokens from API key")
                except Exception as e:
                    logger.error(f"Error deducting tokens: {str(e)}")
                
                logger.info("Successfully completed request")
                return resp_text

# @app.post("/chat/completions")
# async def proxy_chat_completions_test(request: Request):
#     request_body = await request.json()
#     try:
#         request_body = preprocess_request_body(request_body)
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(400, f"Error preprocessing request: {str(e)}")
    
#     for i in range(10):
#         try:
#             account = account_manager.get_next_usable_account()
#             connector = account.get_proxy_connector() # type: ignore
#             async with ClientSession(
#                 timeout=TIMEOUT,
#                 connector=connector if connector else None
#             ) as session:
#                 async with session.get("https://www.google.com") as response:
#                     content = await response.text()
#                     raise Exception("This is a deliberate exception")
#                     return content
#         except Exception as e:
#             continue
        
    
@app.get("/models")
async def list_models():
    """Proxies models request to GitHub Copilot API."""
    logger.info("Received models request")
    
    try:
        max_retries = 3
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                # Get an available account
                account = account_manager.get_next_usable_account()
                if not account:
                    raise ValueError("No usable account available")
                
                # Get the token for this account
                token = await account.get_access_token()
                if not token:
                    raise ValueError("Failed to get access token")
                
                logger.info(f"Using account {account.username} for models request")
                
                # Set up headers
                headers = {
                    "Authorization": f"Bearer {token['token']}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "editor-version": "vscode/1.95.3",
                }
                
                # Get proxy URL if needed
                proxy = get_proxy_url() if RECORD_TRAFFIC else None
                connector = account.get_proxy_connector()
                
                # Create session and make request
                async with ClientSession(
                    timeout=TIMEOUT,
                    connector=connector if connector else None
                ) as session:
                    logger.info(f"Making GET request to {MODELS_API_ENDPOINT}")
                    
                    if session.closed:
                        logger.error("Session is closed")
                        retry_count += 1
                        continue
                        
                    async with session.get(
                        MODELS_API_ENDPOINT,
                        headers=headers,
                        proxy=proxy
                    ) as response:
                        if response.status == 429:
                            error_message = await response.text()
                            logger.warning(f"Rate limit hit: {error_message}")
                            account.mark_rate_limited()
                            retry_count += 1
                            continue
                        elif response.status != 200:
                            error_message = await response.text()
                            logger.error(f"Models API error: {error_message}")
                            if "rate" in error_message.lower():
                                logger.warning(f"Rate limit detected in error: {error_message}")
                                account.mark_rate_limited()
                                retry_count += 1
                                continue
                            raise HTTPException(response.status, f"Models API error: {error_message}")
                        
                        response_data = await response.json()
                        logger.info(f"Successfully fetched models using account {account.username}")
                        return response_data
                        
            except ValueError as e:
                logger.error(str(e))
                raise HTTPException(503, str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error fetching models: {str(traceback.format_exc())}")
                last_error = e
                retry_count += 1
        
        # If we get here, all retries failed
        if last_error:
            raise HTTPException(500, f"Error after {max_retries} retries: {str(last_error)}")
        else:
            raise HTTPException(429, "All retries exhausted due to rate limits")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in models endpoint: {str(e)}")
        raise HTTPException(500, f"Error fetching models: {str(e)}")


@app.post("/chat/completions")
async def proxy_chat_completions(
    request: Request,
    x_api_key: Optional[str] = Header(None)
):
    """Proxies chat completion requests."""
    # Validate API key
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    key_info = api_key_manager.get_key_info(x_api_key)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    if not key_info.enabled:
        raise HTTPException(status_code=403, detail="API key is disabled")
    request_body = await request.json()
    
    # Estimate token usage for credit check
    estimated_tokens = 0
    if "messages" in request_body:
        # Rough estimation: 4 chars = 1 token
        for message in request_body["messages"]:
            content = message.get("content", "")
            if isinstance(content, str):
                estimated_tokens += len(content) // 4
    
    # Add buffer for response tokens
    estimated_tokens += request_body.get("max_tokens", 1000)
    
    # Check if user has sufficient credits
    if not api_key_manager.validate_key(x_api_key, estimated_tokens):
        raise HTTPException(
            status_code=402,
            detail="Insufficient credits for estimated token usage"
        )
    
    # Filter out system messages from the log
    log_request = request_body.copy()
    if "messages" in log_request:
        log_request["messages"] = [
            msg for msg in log_request["messages"] if msg["role"] != "system"
        ]
    
    # Check if this is a streaming request
    stream_mode = request_body.get("stream", False)
    logger.info(f"Request stream mode: {stream_mode}")

    try:
        request_body = preprocess_request_body(request_body)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error preprocessing request: {str(e)}")

    max_retries = 3
    retry_count = 0
    last_error = None

    while retry_count < max_retries:
        try:
            account = account_manager.get_next_usable_account()
            if not account:
                raise ValueError("No usable account available")

            # Make the request with stream parameter
            result = await make_request(account, request_body, x_api_key, stream=stream_mode)
            if result is not None:
                if stream_mode:
                    # Return a streaming response
                    logger.info("Returning streaming response")
                    response = StreamingResponse(
                        result,
                        media_type="text/event-stream",
                        # Add status code explicitly to ensure consistent behavior
                        status_code=200
                    )
                    # Add necessary headers for SSE
                    response.headers["Cache-Control"] = "no-cache"
                    response.headers["Connection"] = "keep-alive"
                    response.headers["X-Accel-Buffering"] = "no"  # Disable buffering for Nginx
                    # Add CORS headers specifically for streaming
                    response.headers["Access-Control-Allow-Origin"] = "*"
                    response.headers["Access-Control-Expose-Headers"] = "*"
                    logger.debug("Configured streaming response with SSE headers")
                    return response
                else:
                    # Return the JSON response
                    logger.info("Returning JSON response")
                    return result

            # If we get here, it means we hit a rate limit
            retry_count += 1
            continue

        except ValueError as e:
            logger.error(str(e))
            raise HTTPException(503, str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in request: {str(traceback.format_exc())}")
            last_error = e
            retry_count += 1

    if last_error:
        raise HTTPException(500, f"Error after {max_retries} retries: {str(last_error)}")
    else:
        raise HTTPException(429, "All retries exhausted due to rate limits")
    
    
