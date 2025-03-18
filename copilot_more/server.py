from contextlib import asynccontextmanager
import json
import asyncio
import traceback

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Optional

from copilot_more.logger import logger
from copilot_more.config import request_timeout, config
from copilot_more.utils import StringSanitizer
from copilot_more.api_key_manager import api_key_manager
from copilot_more.api_routes import router as api_router, get_api_key
from copilot_more.providers import provider_manager

sanitizer = StringSanitizer()

@asynccontextmanager
async def lifespan(app: FastAPI):
    provider_manager.initialize_providers([p.__dict__ for p in config.providers])

    logger.info("Successfully initialized all services")
    yield
    # Clean up here

app = FastAPI(lifespan=lifespan)

# Include API routes
app.include_router(api_router, prefix="/v1", tags=["api"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
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


@app.get("/models")
async def list_models(
    authorization: Optional[str] = Header(None)
):
    """Proxies models request to the first available provider."""
    logger.info("Received models request")
    
    # Validate API key
    api_key = get_api_key(authorization)
    
    try:
        # Make a models request using the provider manager
        result = await provider_manager.make_request(
            request_body={},
            endpoint="models",
            accept_header="application/json",
            stream=False,
            api_key=api_key  # Pass API key for token accounting
        )
        
        if result is None:
            raise HTTPException(503, "No provider available to fulfill the request")
            
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in models endpoint: {str(e)}")
        raise HTTPException(500, f"Error fetching models: {str(e)}")


@app.post("/chat/completions")
async def proxy_chat_completions(
    request: Request,
    authorization: Optional[str] = Header(None)
):
    # Validate API key
    api_key = get_api_key(authorization)
    
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
    
    # Check if user has enough credits
    if not api_key_manager.validate_key(api_key, estimated_tokens):
        raise HTTPException(
            status_code=402,
            detail="Insufficient credits for this request"
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

    try:
        # Make a request using the provider manager
        result = await provider_manager.make_request(
            request_body=request_body,
            endpoint=None,  # Default endpoint (chat/completions)
            accept_header="text/event-stream" if stream_mode else "application/json",
            stream=stream_mode,
            api_key=api_key  # Pass API key for token accounting
        )
        
        if result is None:
            raise HTTPException(503, "No provider available to fulfill the request")
            
        if stream_mode:
            # For streaming responses, deduct based on estimated tokens
            # since we can't know the exact usage until after streaming completes
            api_key_manager.deduct_tokens(api_key, estimated_tokens)
            logger.info(f"Deducted {estimated_tokens} estimated tokens from API key {api_key} for streaming request")
            
            # Return a streaming response
            logger.info("Returning streaming response")
            response = StreamingResponse(
                result,
                media_type="text/event-stream",
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
            
            # Deduct tokens based on actual usage if available
            response_tokens = estimated_tokens  # Default to our estimate
            
            # Check if result has usage information
            if isinstance(result, dict) and "usage" in result and "total_tokens" in result["usage"]:
                response_tokens = result["usage"]["total_tokens"]
            
            # Deduct tokens from the user's credits
            api_key_manager.deduct_tokens(api_key, response_tokens)
            logger.info(f"Deducted {response_tokens} tokens from API key {api_key}")
            
            return result

    except ValueError as e:
        logger.error(str(e))
        raise HTTPException(503, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in request: {str(traceback.format_exc())}")
        raise HTTPException(500, f"Error processing request: {str(e)}")
