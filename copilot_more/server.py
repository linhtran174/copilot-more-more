import asyncio
import json
import time
from datetime import datetime, timezone

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from copilot_more.account_manager import account_manager
from copilot_more.config import request_timeout, token_refresh_interval
from copilot_more.logger import logger
from copilot_more.proxy import RECORD_TRAFFIC, get_proxy_url, initialize_proxy
from copilot_more.token import (get_cached_copilot_token,
                                handle_rate_limit_response,
                                refresh_token_for_account)
from copilot_more.utils import StringSanitizer

sanitizer = StringSanitizer()

initialize_proxy()

# Global variable to track when tokens were last refreshed
last_periodic_refresh = time.time()

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


async def refresh_all_tokens():
    """Refresh tokens for all accounts."""
    for account in account_manager.accounts:
        try:
            token_data = await refresh_token_for_account(account)
            account.update_access_token(token_data["token"], token_data["expires_at"])
            logger.info(f"Periodic token refresh successful for account {account.id}")
        except Exception as e:
            logger.error(f"Failed to refresh token for account {account.id}: {str(e)}")


async def create_client_session() -> ClientSession:
    connector = TCPConnector(ssl=False) if get_proxy_url() else TCPConnector()
    return ClientSession(timeout=TIMEOUT, connector=connector)


async def check_and_refresh_tokens():
    """Check if tokens need periodic refresh and refresh them if needed."""
    global last_periodic_refresh
    current_time = time.time()

    if current_time - last_periodic_refresh >= token_refresh_interval:
        logger.info("Starting periodic token refresh...")
        await refresh_all_tokens()
        last_periodic_refresh = current_time


@app.on_event("startup")
async def startup_event():
    """Start background task for periodic token refresh."""

    async def periodic_refresh():
        while True:
            await check_and_refresh_tokens()
            await asyncio.sleep(60)  # Check every minute

    asyncio.create_task(periodic_refresh())


@app.get("/models")
async def list_models():
    """Proxies models request."""
    try:
        try:
            token = await get_cached_copilot_token()
            account = account_manager.get_account_by_token(token["token"])
            account_id = account.id if account else "unknown"
            logger.info(f"Using account {account_id} for models request")
        except ValueError as e:
            logger.error(f"Failed to get token: {str(e)}")
            raise HTTPException(503, "Service unavailable: No usable tokens available")

        session = await create_client_session()
        async with session as s:
            headers = {
                "Authorization": f"Bearer {token['token']}",
                "Content-Type": "application/json",
                "editor-version": "vscode/1.95.3",
            }
            proxy = get_proxy_url() if RECORD_TRAFFIC else None
            async with s.get(
                MODELS_API_ENDPOINT, headers=headers, proxy=proxy
            ) as response:
                if response.status != 200:
                    error_message = await response.text()
                    logger.error(
                        f"Models API error for token {token['token'][:8]}...: {error_message}"
                    )
                    raise HTTPException(
                        response.status, f"Models API error: {error_message}"
                    )
                response_data = await response.json()
                logger.info(
                    f"Successfully fetched models using account {account_id}..."
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

    async def stream_response():
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                try:
                    token = await get_cached_copilot_token()
                    account = account_manager.get_account_by_token(token["token"])
                    account_id = account.id if account else "unknown"
                    logger.info(f"Using account {account_id} for request")
                except ValueError as e:
                    logger.error(f"Failed to get token: {str(e)}")
                    yield json.dumps(
                        {"error": "No usable tokens available - service unavailable"}
                    ).encode("utf-8")
                    return

                model = request_body.get("model", "")
                is_streaming = request_body.get("stream", False)

                session = await create_client_session()
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
                        proxy=proxy,
                    ) as response:
                        if response.status == 429:  # Rate limit error
                            error_message = await response.text()
                            logger.warning(
                                f"Rate limit hit for account {account_id}: {error_message}"
                            )
                            handle_rate_limit_response(token["token"])
                            retry_count += 1
                            if retry_count < max_retries:
                                logger.info(
                                    f"Retrying request with a new token (attempt {retry_count + 1}/{max_retries})"
                                )
                                continue
                            else:
                                logger.error(
                                    f"All tokens are rate limited after {max_retries} retries"
                                )
                                raise HTTPException(429, "All tokens are rate limited")
                        elif response.status != 200:
                            error_message = await response.text()
                            logger.error(
                                f"API error for account {account_id}: {error_message}"
                            )
                            if (
                                "rate" in error_message.lower()
                            ):  # Check for rate limit in error message
                                logger.warning(
                                    f"Rate limit detected in error for account {account_id}: {error_message}"
                                )
                                handle_rate_limit_response(token["token"])
                                retry_count += 1
                                if retry_count < max_retries:
                                    logger.info(
                                        f"Retrying request with a new token (attempt {retry_count + 1}/{max_retries})"
                                    )
                                    continue
                            elif (
                                "unauthorized" in error_message.lower()
                                or "forbidden" in error_message.lower()
                                or "token" in error_message.lower()
                            ):
                                # Try to refresh token on authorization errors
                                logger.warning(
                                    f"Authorization error detected, refreshing token for account {account_id}"
                                )
                                account = account_manager.get_account_by_token(
                                    token["token"]
                                )
                                if account:
                                    try:
                                        new_token = await refresh_token_for_account(
                                            account
                                        )
                                        account.update_access_token(
                                            new_token["token"], new_token["expires_at"]
                                        )
                                        retry_count += 1
                                        if retry_count < max_retries:
                                            logger.info(
                                                f"Token refreshed, retrying request (attempt {retry_count + 1}/{max_retries})"
                                            )
                                            continue
                                    except Exception as e:
                                        logger.error(
                                            f"Failed to refresh token: {str(e)}"
                                        )
                            raise HTTPException(
                                response.status, f"API error: {error_message}"
                            )

                        if model.startswith("o1") and is_streaming:
                            # For o1 models with streaming, read entire response and convert to SSE
                            data = await response.json()
                            converted_data = convert_o1_response(data)
                            for event in convert_to_sse_events(converted_data):
                                yield event.encode("utf-8")
                        else:
                            # For other cases, stream chunks directly
                            async for chunk in response.content.iter_chunks():
                                if chunk:
                                    chunk_data = chunk[0].decode("utf-8")
                                    # Log chunks that contain response data
                                    if (
                                        "content" in chunk_data
                                        and not chunk_data.startswith("data: [DONE]")
                                    ):
                                        try:
                                            parsed = json.loads(
                                                chunk_data.replace("data: ", "")
                                            )
                                            if (
                                                "choices" in parsed
                                                and parsed["choices"]
                                            ):
                                                content = (
                                                    parsed["choices"][0]
                                                    .get("delta", {})
                                                    .get("content")
                                                )
                                                if content:
                                                    logger.info(
                                                        f"API Response content from account {account_id}: {content}"
                                                    )
                                        except json.JSONDecodeError:
                                            pass
                                    yield chunk[0]
                logger.info(
                    f"Successfully processed chat completion request using account {account_id}..."
                )
                return  # Successfully processed request
            except Exception as e:
                if isinstance(e, asyncio.TimeoutError):
                    logger.error("Request timed out")
                    yield json.dumps(
                        {"error": "Request timed out after 10 seconds"}
                    ).encode("utf-8")
                else:
                    logger.error(f"Error in stream_response: {str(e)}")
                    yield json.dumps({"error": str(e)}).encode("utf-8")
                return

    return StreamingResponse(stream_response(), media_type="text/event-stream")
