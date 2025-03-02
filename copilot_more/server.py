import json
import asyncio
import traceback

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from copilot_more.account_manager import account_manager
from copilot_more.logger import logger
from copilot_more.config import request_timeout
from copilot_more.proxy import RECORD_TRAFFIC, get_proxy_url
from copilot_more.utils import StringSanitizer

sanitizer = StringSanitizer()

app = FastAPI()

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
    return {**request_body, "messages": processed_messages, "max_tokens": max_tokens}

async def make_request(account, request_body, endpoint=CHAT_COMPLETIONS_API_ENDPOINT, accept_header="text/event-stream"):
    """Make a single request with proper session handling."""
    logger.info("Fetching access token")
    token = await account.get_access_token()
    if not token:
        raise ValueError("Failed to get access token")

    headers = {
        "Authorization": f"Bearer {token['token']}",
        "Content-Type": "application/json",
        "Accept": accept_header,
        "editor-version": "vscode/1.95.3",
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
                return None
            elif response.status != 200:
                error_message = await response.text()
                logger.error(f"API error: {error_message}")
                if "rate" in error_message.lower():
                    logger.warning(f"Rate limit detected in error: {error_message}")
                    account.mark_rate_limited()
                    return None
                raise HTTPException(response.status, f"API error: {error_message}")
            
            resp_text = await response.json()
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
async def proxy_chat_completions(request: Request):
    """Proxies chat completion requests."""
    request_body = await request.json()

    # Filter out system messages from the log
    log_request = request_body.copy()
    if "messages" in log_request:
        log_request["messages"] = [
            msg for msg in log_request["messages"] if msg["role"] != "system"
        ]
    # logger.info(f"Received request: {json.dumps(log_request, indent=2)}")
    

    try:
        request_body = preprocess_request_body(request_body)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error preprocessing request: {str(e)}")

    max_retries = 3
    retry_count = 0
    last_error = None

    result = None
    while retry_count < max_retries:
        try:
            account = account_manager.get_next_usable_account()
            if not account:
                raise ValueError("No usable account available")

            result = await make_request(account, request_body)
            if result is not None:
                break;

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

    if result is not None:
        return result

    if last_error:
        raise HTTPException(500, f"Error after {max_retries} retries: {str(last_error)}")
    else:
        raise HTTPException(429, "All retries exhausted due to rate limits")
    
    
