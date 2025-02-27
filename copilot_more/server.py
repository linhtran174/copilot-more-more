import json
import asyncio
import traceback

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from copilot_more.account_manager import account_manager
from copilot_more.logger import logger
from copilot_more.config import request_timeout
# from copilot_more.proxy import RECORD_TRAFFIC, get_proxy_url, initialize_proxy
from copilot_more.proxy import RECORD_TRAFFIC, get_proxy_url
from copilot_more.utils import StringSanitizer

sanitizer = StringSanitizer()

# initialize_proxy()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHAT_COMPLETIONS_API_ENDPOINT = (
    "https://api.individual.githubcopilot.com/chat/completions"
)
MODELS_API_ENDPOINT = "https://api.individual.githubcopilot.com/models"
TIMEOUT = ClientTimeout(total=request_timeout)  # Default timeout from config
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


def convert_o1_response(data: dict) -> dict:
    """Convert o1 model response format to standard format"""
    if "choices" not in data:
        return data

    choices = data["choices"]
    if not choices:
        return data

    converted_choices = []
    for choice in choices:
        if "message" in choice:
            converted_choice = {
                "index": choice["index"],
                "delta": {"content": choice["message"]["content"]},
            }
            if "finish_reason" in choice:
                converted_choice["finish_reason"] = choice["finish_reason"]
            converted_choices.append(converted_choice)

    return {**data, "choices": converted_choices}


def convert_to_sse_events(data: dict) -> list[str]:
    """Convert response data to SSE events"""
    events = []
    if "choices" in data:
        for choice in data["choices"]:
            event_data = {
                "id": data.get("id", ""),
                "created": data.get("created", 0),
                "model": data.get("model", ""),
                "choices": [choice],
            }
            events.append(f"data: {json.dumps(event_data)}\n\n")
    events.append("data: [DONE]\n\n")
    return events

@app.get("/models")
async def list_models():
    """Proxies models request."""
    try:
        try:
            account = account_manager.get_next_usable_account()
            if not account:
                raise ValueError("No usable account available")

            token = await account.get_access_token()
            if not token:
                raise ValueError("Failed to get access token")

        except ValueError as e:
            logger.error(f"Failed to get token: {str(e)}")
            raise HTTPException(503, "Service unavailable: No usable tokens available")

        connector = account.get_proxy_connector()
        if not connector:
            raise HTTPException(500, "Failed to get proxy connector")

        session = ClientSession(timeout=TIMEOUT, connector=connector)
        async with session as s:
            headers = {
                "Authorization": f"Bearer {token['token']}",
                "Content-Type": "application/json",
                "editor-version": "vscode/1.95.3",
            }
            proxy = get_proxy_url() if RECORD_TRAFFIC else None
            async with s.get(MODELS_API_ENDPOINT, headers=headers, proxy=proxy) as response:
                if response.status != 200:
                    error_message = await response.text()
                    logger.error(f"Models API error: {error_message}")
                    raise HTTPException(
                        response.status, f"Models API error: {error_message}"
                    )
                response_data = await response.json()
                logger.info(
                    f"Models API response: {json.dumps(response_data, indent=2)}"
                )
                return response_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching models: {str(e)}")
        raise HTTPException(500, f"Error fetching models: {str(e)}")


@app.post("/chat/completions")
async def proxy_chat_completions(request: Request):
    """Proxies chat completion requests with SSE support."""
    request_body = await request.json()

    # Filter out system messages from the log while preserving the original request
    log_request = request_body.copy()
    if "messages" in log_request:
        log_request["messages"] = [
            msg for msg in log_request["messages"] if msg["role"] != "system"
        ]
    logger.info(f"Received request: {json.dumps(log_request, indent=2)}")

    try:
        request_body = preprocess_request_body(request_body)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(400, f"Error preprocessing request: {str(e)}")

    # async def stream_response():
    #     max_retries = 3
    #     retry_count = 0

    #     while retry_count < max_retries:
    #         try:
    #             try:
    #                 account = account_manager.get_next_usable_account()
    #                 token = await account.get_access_token() # type: ignore
    #             except ValueError as e:
    #                 logger.error(f"Failed to get token: {str(e)}")
    #                 yield json.dumps(
    #                     {"error": "No usable tokens available - service unavailable"}
    #                 ).encode("utf-8")
    #                 return

    #             model = request_body.get("model", "")
    #             is_streaming = request_body.get("stream", False)

    #             session = ClientSession(
    #                 timeout=TIMEOUT, connector=account.get_proxy_connector() # type: ignore
    #             ) 
    #             async with session as s:
    #                 headers = {
    #                     "Authorization": f"Bearer {token['token']}",
    #                     "Content-Type": "application/json",
    #                     "Accept": "text/event-stream",
    #                     "editor-version": "vscode/1.95.3",
    #                 }
    #                 proxy = get_proxy_url() if RECORD_TRAFFIC else None
    #                 async with s.post(
    #                     CHAT_COMPLETIONS_API_ENDPOINT,
    #                     json=request_body,
    #                     headers=headers,
    #                     proxy=proxy
    #                 ) as response:
    #                     if response.status == 429:  # Rate limit error
    #                         error_message = await response.text()
    #                         logger.warning(f"Rate limit hit: {error_message}")
    #                         account.mark_rate_limited()
    #                         retry_count += 1
    #                         if retry_count < max_retries:
    #                             logger.info(
    #                                 f"Retrying request with a new token (attempt {retry_count + 1}/{max_retries})"
    #                             )
    #                             continue
    #                         else:
    #                             logger.error(
    #                                 "All available tokens are rate limited - no more retries"
    #                             )
    #                             raise HTTPException(429, "All tokens are rate limited")
    #                     elif response.status != 200:
    #                         error_message = await response.text()
    #                         logger.error(f"API error: {error_message}")
    #                         if (
    #                             "rate" in error_message.lower()
    #                         ):  # Check for rate limit in error message
    #                             logger.warning(
    #                                 f"Rate limit detected in error: {error_message}"
    #                             )
    #                             account.mark_rate_limited()
    #                             retry_count += 1
    #                             if retry_count < max_retries:
    #                                 logger.info(
    #                                     f"Retrying request with a new token (attempt {retry_count + 1}/{max_retries})"
    #                                 )
    #                                 continue
    #                         raise HTTPException(
    #                             response.status, f"API error: {error_message}"
    #                         )

    #                     if model.startswith("o1") and is_streaming:
    #                         # For o1 models with streaming, read entire response and convert to SSE
    #                         data = await response.json()
    #                         converted_data = convert_o1_response(data)
    #                         for event in convert_to_sse_events(converted_data):
    #                             yield event.encode("utf-8")
    #                     else:
    #                         # For other cases, stream chunks directly
    #                         async for chunk in response.content.iter_chunks():
    #                             if chunk:
    #                                 chunk_data = chunk[0].decode("utf-8")
    #                                 # Log chunks that contain response data
    #                                 if (
    #                                     "content" in chunk_data
    #                                     and not chunk_data.startswith("data: [DONE]")
    #                                 ):
    #                                     try:
    #                                         parsed = json.loads(
    #                                             chunk_data.replace("data: ", "")
    #                                         )
    #                                         if (
    #                                             "choices" in parsed
    #                                             and parsed["choices"]
    #                                         ):
    #                                             content = (
    #                                                 parsed["choices"][0]
    #                                                 .get("delta", {})
    #                                                 .get("content")
    #                                             )
    #                                             if content:
    #                                                 logger.info(
    #                                                     f"API Response content: {content}"
    #                                                 )
    #                                     except json.JSONDecodeError:
    #                                         pass
    #                                 yield chunk[0]
    #             logger.info("Successfully processed chat completion request")
    #             return  # Successfully processed request
    #         except Exception as e:
    #             if isinstance(e, asyncio.TimeoutError):
    #                 logger.error("Request timed out")
    #                 yield json.dumps(
    #                     {"error": "Request timed out after 10 seconds"}
    #                 ).encode("utf-8")
    #             else:
    #                 logger.error(f"Error in stream_response: {str(e)}")
    #                 yield json.dumps({"error": str(e)}).encode("utf-8")
    #             return
    
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            try:
                account = account_manager.get_next_usable_account()
                if not account:
                    raise ValueError("No usable account available")
                
                token = await account.get_access_token()
                if not token:
                    continue

            except ValueError as e:
                logger.error(f"Failed to get token: {str(e)}")
                raise HTTPException(503, "Service unavailable: No usable tokens available")

            model = request_body.get("model", "")
            is_streaming = request_body.get("stream", False)

            connector = account.get_proxy_connector()
            if not connector:
                raise HTTPException(500, "Failed to get proxy connector")

            session = ClientSession(timeout=TIMEOUT, connector=connector)
            async with session as s:
                headers = {
                    "Authorization": f"Bearer {token['token']}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "editor-version": "vscode/1.95.3",
                }
                proxy = get_proxy_url() if RECORD_TRAFFIC else None
                async with s.post(
                    CHAT_COMPLETIONS_API_ENDPOINT,
                    json=request_body,
                    headers=headers,
                    # proxy=proxy
                ) as response:
                    if response.status == 429:  # Rate limit error
                        error_message = await response.text()
                        logger.warning(f"Rate limit hit: {error_message}")
                        account.mark_rate_limited()
                        retry_count += 1
                        if retry_count < max_retries:
                            logger.info(
                                f"Retrying request with a new token (attempt {retry_count + 1}/{max_retries})"
                            )
                            continue
                        else:
                            logger.error(
                                "All available tokens are rate limited - no more retries"
                            )
                            raise HTTPException(429, "All tokens are rate limited")
                    elif response.status != 200:
                        error_message = await response.text()
                        logger.error(f"API error: {error_message}")
                        if (
                            "rate" in error_message.lower()
                        ):  # Check for rate limit in error message
                            logger.warning(
                                f"Rate limit detected in error: {error_message}"
                            )
                            account.mark_rate_limited()
                            retry_count += 1
                            if retry_count < max_retries:
                                logger.info(
                                    f"Retrying request with a new token (attempt {retry_count + 1}/{max_retries})"
                                )
                                continue
                        raise HTTPException(
                            response.status, f"API error: {error_message}"
                        )
                    
                    respText = await response.json()
                    logger.info(f"API Response: {respText}")
                    return respText
        except Exception as e:
            if isinstance(e, asyncio.TimeoutError):
                logger.error("Request timed out")
            else:
                logger.error(f"Error in stream_response: {str(traceback.format_exc())}")
            return


    # return StreamingResponse(stream_response(), media_type="text/event-stream")
    # return stream_response()

