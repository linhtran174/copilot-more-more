import json
from typing import Dict, Optional
from urllib.parse import urlparse

from aiohttp import ClientSession
from aiohttp_socks import ProxyConnector

from copilot_more.account_manager import account_manager
from copilot_more.logger import logger


async def refresh_token_for_account(account) -> Dict:
    """Refresh token from GitHub Copilot API for a specific account."""
    logger.info(f"Attempting to refresh token for account {account.id}")

    connector = None
    if account.proxy_config:
        if account.proxy_config.username and account.proxy_config.password:
            connector = ProxyConnector.from_url(
                f"socks5://{account.proxy_config.username}:{account.proxy_config.password}@{account.proxy_config.host}:{account.proxy_config.port}"
            )
        else:
            connector = ProxyConnector.from_url(
                f"socks5://{account.proxy_config.host}:{account.proxy_config.port}"
            )

    async with ClientSession(connector=connector) as session:
        async with session.get(
            url="https://api.github.com/copilot_internal/v2/token",
            headers={
                "Authorization": f"token {account.refresh_token}",
                "editor-version": "vscode/1.95.3",
            },
        ) as response:
            if response.status == 200:
                token_data = await response.json()
                logger.info(
                    f"Token refreshed successfully for account {account.id}, expires at {token_data.get('expires_at')}"
                )
                return token_data
            error_msg = f"Failed to refresh token for account {account.id}: {response.status} {await response.text()}"
            logger.error(error_msg)
            raise ValueError(error_msg)


async def get_cached_copilot_token() -> Dict:
    """Get a valid token from an account, refreshing if needed."""
    account = account_manager.get_next_usable_account()
    if not account:
        raise ValueError("No usable accounts available")

    if account.access_token and account.access_token.is_valid():
        logger.info(f"Using existing token from account {account.id}")
        return {
            "token": account.access_token.token,
            "expires_at": account.access_token.expires_at,
        }

    logger.info(f"Getting fresh token for account {account.id}...")
    new_token = await refresh_token_for_account(account)
    account.update_access_token(new_token["token"], new_token["expires_at"])
    return new_token


def handle_rate_limit_response(token: str):
    """Handle rate limit response by marking the account as rate-limited."""
    account = account_manager.get_account_by_token(token)
    if account:
        account.mark_rate_limited()
