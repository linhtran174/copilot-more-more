import json
import threading
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from urllib.parse import quote

from aiohttp import ClientSession
from aiohttp_socks import ProxyConnector

from copilot_more.logger import logger
from copilot_more.config import account_configs, ProxyConfig, RateLimitWindow, DEFAULT_RATE_LIMIT_WINDOWS


@dataclass
class AccessToken:
    """Represents a GitHub Copilot access token."""
    token: str
    expires_at: int
    rate_limited_until: int = 0
    last_used: int = 0

    def is_valid(self) -> bool:
        """Check if token is not expired (with 5 minute buffer)."""
        return self.expires_at > time.time() + 60


class AccountInfo:
    """Manages a GitHub Copilot account's refresh and access tokens."""

    def __init__(self, refresh_token: str, username: str, proxy_config: Optional[ProxyConfig] = None, rate_limit_windows: Optional[List[RateLimitWindow]] = None):
        self.username = username
        self.refresh_token = refresh_token
        self.rate_limited_until = 0
        self._bad_credentials = False
        self.access_token: Optional[AccessToken] = None
        self.proxy_config = proxy_config
        self.last_used = 0
        self.rate_limit_windows = rate_limit_windows or DEFAULT_RATE_LIMIT_WINDOWS
        self.request_timestamps: List[float] = []

    def update_access_token(self, token: str, expires_at: int):
        """Update the account's access token."""
        self.access_token = AccessToken(token, expires_at)
        logger.info(f"Updated access token for account {self.username}, expires at {expires_at}")

    def is_usable(self) -> bool:
        """Check if the account has a valid, non-rate-limited access token."""
        if not self.access_token:
            return True  # No token yet, so can be used to get one
        return not self.is_rate_limited() and not self._bad_credentials

    def mark_rate_limited(self, duration: int = 60):
        self.rate_limited_until = time.time() + duration
        logger.warning(f"Account {self.username} marked as rate limited for {duration} seconds")

    def record_request(self):
        """Record a new API request."""
        current_time = time.time()
        self.request_timestamps.append(current_time)
        
        # Remove timestamps outside the largest window to prevent unbounded growth
        max_window = max(window.duration for window in self.rate_limit_windows)
        cutoff_time = current_time - max_window
        self.request_timestamps = [ts for ts in self.request_timestamps if ts > cutoff_time]

    def is_rate_limited(self) -> bool:
        """Check if account is rate limited by any window or external limit."""
        current_time = time.time()
        
        # Check external rate limit first (from API responses)
        if self.rate_limited_until > 0 and current_time < self.rate_limited_until:
            return True

        # Clean up old timestamps
        max_window = max(window.duration for window in self.rate_limit_windows)
        cutoff_time = current_time - max_window
        self.request_timestamps = [ts for ts in self.request_timestamps if ts > cutoff_time]

        # Check each rate limit window
        for window in self.rate_limit_windows:
            window_start = current_time - window.duration
            requests_in_window = sum(1 for ts in self.request_timestamps if ts > window_start)
            
            if requests_in_window >= window.max_requests:
                logger.warning(
                    f"Account {self.username} rate limited: {requests_in_window} requests "
                    f"in {window.duration}s window (max: {window.max_requests})"
                )
                return True

        return False
        
    async def get_access_token(self) -> Optional[Dict[str, str]]:
        """Get the current access token if available."""
        if self.access_token and self.access_token.is_valid():
            return {"token": self.access_token.token}
        logger.info(f"Getting fresh token for account {self.username}")
        try: 
            new_token = await self.refresh_access_token()
            self.update_access_token(new_token["token"], new_token["expires_at"])
            return new_token
        except Exception as e:
            logger.error(f"Failed to get access token for {self.username}: {str(e)}")
            return None
    
    async def refresh_access_token(self) -> Dict:
        """Refresh the account's access token."""
        logger.info(f"Attempting to refresh token for account {self.username}")
        connector = None
        session = None
        try:
            if self.proxy_config:
                if self.proxy_config.username and self.proxy_config.password:
                    connector = ProxyConnector.from_url(
                        f'socks5://{self.proxy_config.username}:{self.proxy_config.password}@{self.proxy_config.host}:{self.proxy_config.port}'
                    )
                else:
                    connector = ProxyConnector.from_url(
                        f'socks5://{self.proxy_config.host}:{self.proxy_config.port}'
                    )

            session = ClientSession(connector=connector)
            logger.debug("Created session for token refresh")
            
            async with session:
                async with session.get(
                    url="https://api.github.com/copilot_internal/v2/token",
                    headers={
                        "Authorization": f"token {self.refresh_token}",
                        "editor-version": "vscode/1.95.3",
                    },
                ) as response:
                    response_text = await response.text()
                    if response.status == 200:
                        token_data = await response.json()
                        logger.info(
                            f"Token refreshed successfully for {self.username}, expires at {token_data.get('expires_at')}"
                        )
                        return token_data

                    if response.status == 401 or "Bad credentials" in response_text:
                        self._bad_credentials = True
                        error_msg = f"Bad credentials for account {self.username}"
                    else:
                        error_msg = f"Failed to refresh token for account {self.username}: Status {response.status} - {response_text}"
                    self._bad_credentials = True
                    raise Exception(error_msg)
        except Exception as e:
            logger.error(f"Error during token refresh for {self.username}: {str(e)}")
            raise
        finally:
            if session and not session.closed:
                await session.close()
                logger.debug("Closed session after token refresh")
        
    def get_proxy_connector(self) -> Optional[ProxyConnector]:
        if self.proxy_config:
            if self.proxy_config.username and self.proxy_config.password:
                self._proxy_connector = ProxyConnector.from_url(
                    f'socks5://{self.proxy_config.username}:{self.proxy_config.password}@{self.proxy_config.host}:{self.proxy_config.port}'
                )
            else:
                self._proxy_connector = ProxyConnector.from_url(
                    f'socks5://{self.proxy_config.host}:{self.proxy_config.port}'
                )
        return None
         
class AccountManager:
    """Manages multiple GitHub Copilot accounts and their tokens."""

    def __init__(self):
        self.accounts: List[AccountInfo] = []
        self.current_index = 0
        self.lock = threading.Lock()

    def add_account(self, refresh_token: str, username: str, proxy_config: Optional[ProxyConfig] = None, rate_limit_windows: Optional[List[RateLimitWindow]] = None):
        """Add a new account using its refresh token, proxy configuration, and rate limit windows."""
        with self.lock:
            # Check if account already exists
            if not any(acc.refresh_token == refresh_token for acc in self.accounts):
                account = AccountInfo(refresh_token, username, proxy_config, rate_limit_windows)
                self.accounts.append(account)
                logger.info(f"Added new account {username} to manager")

    def get_next_usable_account(self) -> Optional[AccountInfo]:
        """Get the next account that can be used, in round-robin fashion."""
        if not self.accounts:
            return None

        with self.lock:
            checked_count = 0
            while checked_count < len(self.accounts):
                current_account = self.accounts[self.current_index]

                if current_account.is_usable():
                    current_account.last_used = int(time.time())
                    # Only increment index after finding a usable account
                    self.current_index = (self.current_index + 1) % len(self.accounts)
                    logger.info(f"Account {current_account.username} is being used")
                    return current_account

                # Move to next account
                self.current_index = (self.current_index + 1) % len(self.accounts)
                checked_count += 1

            logger.warning("No usable accounts found after checking all accounts")
            return None

    def get_account_by_token(self, access_token: str) -> Optional[AccountInfo]:
        """Find account by its current access token."""
        for account in self.accounts:
            if account.access_token and account.access_token.token == access_token:
                return account
        return None

    def handle_rate_limit(self, access_token: str):
        """Mark an account as rate-limited when a 429 response is received."""
        account = self.get_account_by_token(access_token)
        if account:
            account.mark_rate_limited()
            logger.warning(f"Account {account.username} marked as rate limited due to 429 response")


# Global account manager instance
account_manager = AccountManager()

# Initialize accounts from config
if not account_configs:
    logger.error("No account configurations available - accounts cannot be initialized")
else:
    for config in account_configs:
        account_manager.add_account(
            refresh_token=config.refresh_token,
            username=config.username,
            proxy_config=config.proxy,
            rate_limit_windows=config.rate_limit_windows
        )
    logger.info(f"Successfully initialized {len(account_manager.accounts)} accounts")

if not account_manager.accounts:
    logger.error("No accounts were initialized - service may not function correctly")
