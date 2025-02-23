import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

from copilot_more.config import ProxyConfig, account_configs
from copilot_more.logger import logger


@dataclass
class AccessToken:
    """Represents a GitHub Copilot access token."""

    token: str
    expires_at: int
    rate_limited_until: int = 0
    last_used: int = 0

    def is_valid(self) -> bool:
        """Check if token is not expired (with 5 minute buffer)."""
        return self.expires_at > time.time() + 300

    def is_rate_limited(self) -> bool:
        """Check if token is currently rate limited."""
        return time.time() < self.rate_limited_until

    def mark_rate_limited(self, duration: int = 60):
        """Mark token as rate limited for the specified duration."""
        self.rate_limited_until = int(time.time()) + duration


class AccountInfo:
    """Manages a GitHub Copilot account's refresh and access tokens."""

    def __init__(
        self,
        id: str,
        password: str,
        refresh_token: str,
        proxy_config: Optional[ProxyConfig] = None,
    ):
        self.id = id
        self.password = password
        self.refresh_token = refresh_token
        self.access_token: Optional[AccessToken] = None
        self.proxy_config = proxy_config
        self.last_used = 0

    @property
    def proxies(self) -> Optional[Dict[str, str]]:
        """Get the proxy configuration in the format required by requests library."""
        if not self.proxy_config:
            return None

        auth = ""
        if self.proxy_config.username:
            auth = f"{quote(self.proxy_config.username)}"
            if self.proxy_config.password:
                auth += f":{quote(self.proxy_config.password)}"
            auth += "@"

        proxy_url = f"socks5://{auth}{self.proxy_config.host}:{self.proxy_config.port}"
        return {"http": proxy_url, "https": proxy_url}

    def update_access_token(self, token: str, expires_at: int):
        """Update the account's access token."""
        old_token = self.access_token
        # Create new token with fresh state (not rate limited)
        self.access_token = AccessToken(token, expires_at)
        
        if old_token:
            was_rate_limited = old_token.is_rate_limited()
            logger.info(
                f"Updated access token for account {self.id}:\n"
                f"  Old token expired at: {datetime.fromtimestamp(old_token.expires_at)}\n"
                f"  New token expires at: {datetime.fromtimestamp(expires_at)}\n"
                f"  Old token was rate limited: {was_rate_limited}\n"
                f"  Rate limit cleared on refresh: True"
            )
            if was_rate_limited:
                logger.info(f"Rate limit state cleared during token refresh for account {self.id}")
        else:
            logger.info(
                f"Initial access token set for account {self.id}, "
                f"expires at {datetime.fromtimestamp(expires_at)}"
            )

    def is_usable(self) -> bool:
        """Check if the account has a valid, non-rate-limited access token."""
        if not self.access_token:
            logger.debug(f"Account {self.id} has no access token, considered usable")
            return True  # No token yet, so can be used to get one
        
        is_rate_limited = self.access_token.is_rate_limited()
        is_valid = self.access_token.is_valid()
        
        logger.debug(
            f"Account {self.id} status check - "
            f"Rate limited: {is_rate_limited}, "
            f"Token valid: {is_valid}, "
            f"Token expires at: {datetime.fromtimestamp(self.access_token.expires_at)}, "
            f"Rate limited until: {datetime.fromtimestamp(self.access_token.rate_limited_until) if self.access_token.rate_limited_until > 0 else 'Not rate limited'}"
        )
        
        return not is_rate_limited and is_valid

    def mark_rate_limited(self, duration: int = 60):
        """Mark the account's current access token as rate limited."""
        if self.access_token:
            old_limit = self.access_token.rate_limited_until
            current_time = int(time.time())
            self.access_token.mark_rate_limited(duration)
            
            logger.warning(
                f"Account {self.id} rate limit status:\n"
                f"  Current time: {datetime.fromtimestamp(current_time)}\n"
                f"  Previous limit until: {datetime.fromtimestamp(old_limit) if old_limit > 0 else 'Not rate limited'}\n"
                f"  New limit until: {datetime.fromtimestamp(self.access_token.rate_limited_until)}\n"
                f"  Token expires at: {datetime.fromtimestamp(self.access_token.expires_at)}\n"
                f"  Duration: {duration} seconds"
            )


class AccountManager:
    """Manages multiple GitHub Copilot accounts and their tokens."""

    def __init__(self):
        self.accounts: List[AccountInfo] = []
        self.current_index = 0
        self.lock = threading.Lock()

    def add_account(
        self,
        id: str,
        password: str,
        refresh_token: str,
        proxy_config: Optional[ProxyConfig] = None,
    ):
        """Add a new account using its id, password, refresh token and optional proxy configuration."""
        with self.lock:
            # Check if account already exists
            if not any(acc.refresh_token == refresh_token for acc in self.accounts):
                self.accounts.append(
                    AccountInfo(id, password, refresh_token, proxy_config)
                )
                logger.info(f"Added new account {id} to manager")

    def get_next_usable_account(self) -> Optional[AccountInfo]:
        """Get the next account that can be used, in round-robin fashion."""
        if not self.accounts:
            return None

        with self.lock:
            checked_count = 0
            start_index = self.current_index
            logger.debug(f"Starting account search from index {start_index}")
            
            while checked_count < len(self.accounts):
                current_account = self.accounts[self.current_index]
                logger.debug(f"Checking account {current_account.id} at index {self.current_index}")

                if current_account.is_usable():
                    current_account.last_used = int(time.time())
                    # Only increment index after finding a usable account
                    self.current_index = (self.current_index + 1) % len(self.accounts)
                    logger.debug(f"Found usable account {current_account.id}, next index will be {self.current_index}")
                    return current_account

                # Move to next account
                self.current_index = (self.current_index + 1) % len(self.accounts)
                checked_count += 1

            # Log detailed state of all accounts when none are usable
            logger.warning("No usable accounts found after checking all accounts")
            for idx, account in enumerate(self.accounts):
                if account.access_token:
                    logger.warning(
                        f"Account {account.id} (index {idx}) status:\n"
                        f"  Token expires at: {datetime.fromtimestamp(account.access_token.expires_at)}\n"
                        f"  Rate limited until: {datetime.fromtimestamp(account.access_token.rate_limited_until) if account.access_token.rate_limited_until > 0 else 'Not rate limited'}\n"
                        f"  Last used: {datetime.fromtimestamp(account.last_used) if account.last_used > 0 else 'Never'}"
                    )
                else:
                    logger.warning(f"Account {account.id} (index {idx}) has no access token")
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
            logger.warning(f"Account marked as rate limited due to 429 response")


# Global account manager instance
account_manager = AccountManager()

# Initialize accounts from config
if not account_configs:
    logger.error("No account configurations available - accounts cannot be initialized")
else:
    for config in account_configs:
        account_manager.add_account(
            id=config.id,
            password=config.password,
            refresh_token=config.token,
            proxy_config=config.proxy,
        )
    logger.info(f"Successfully initialized {len(account_manager.accounts)} accounts")

if not account_manager.accounts:
    logger.error("No accounts were initialized - service may not function correctly")
