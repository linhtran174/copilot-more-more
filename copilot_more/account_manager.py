import json
import threading
import time
from typing import Dict, List, Optional
from dataclasses import dataclass

from copilot_more.logger import logger
from copilot_more.config import refresh_tokens


@dataclass
class AccessToken:
    """Represents a GitHub Copilot access token."""

    token: str
    expires_at: int
    rate_limited_until: int = 0
    last_used: float = 0

    def is_valid(self) -> bool:
        """Check if token is not expired (with 5 minute buffer)."""
        return self.expires_at > time.time() + 300

    def is_rate_limited(self) -> bool:
        """Check if token is currently rate limited."""
        return time.time() < self.rate_limited_until

    def mark_rate_limited(self, duration: int = 60):
        """Mark token as rate limited for the specified duration."""
        self.rate_limited_until = time.time() + duration


class AccountInfo:
    """Manages a GitHub Copilot account's refresh and access tokens."""

    def __init__(self, refresh_token: str):
        self.refresh_token = refresh_token
        self.access_token: Optional[AccessToken] = None
        self.last_used = 0

    def update_access_token(self, token: str, expires_at: int):
        """Update the account's access token."""
        self.access_token = AccessToken(token, expires_at)
        logger.info(f"Updated access token for account, expires at {expires_at}")

    def is_usable(self) -> bool:
        """Check if the account has a valid, non-rate-limited access token."""
        if not self.access_token:
            return True  # No token yet, so can be used to get one
        return not self.access_token.is_rate_limited() and self.access_token.is_valid()

    def mark_rate_limited(self, duration: int = 60):
        """Mark the account's current access token as rate limited."""
        if self.access_token:
            self.access_token.mark_rate_limited(duration)
            logger.warning(f"Account marked as rate limited for {duration} seconds")


class AccountManager:
    """Manages multiple GitHub Copilot accounts and their tokens."""

    def __init__(self):
        self.accounts: List[AccountInfo] = []
        self.current_index = 0
        self.lock = threading.Lock()

    def add_account(self, refresh_token: str):
        """Add a new account using its refresh token."""
        with self.lock:
            # Check if account already exists
            if not any(acc.refresh_token == refresh_token for acc in self.accounts):
                self.accounts.append(AccountInfo(refresh_token))
                logger.info("Added new account to manager")

    def get_next_usable_account(self) -> Optional[AccountInfo]:
        """Get the next account that can be used, in round-robin fashion."""
        if not self.accounts:
            return None

        with self.lock:
            checked_count = 0
            while checked_count < len(self.accounts):
                current_account = self.accounts[self.current_index]

                if current_account.is_usable():
                    current_account.last_used = time.time()
                    # Only increment index after finding a usable account
                    self.current_index = (self.current_index + 1) % len(self.accounts)
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
            logger.warning(f"Account marked as rate limited due to 429 response")


# Global account manager instance
account_manager = AccountManager()

# Initialize accounts from config
if not refresh_tokens:
    logger.error("No refresh tokens available - accounts cannot be initialized")
else:
    for token in refresh_tokens:
        account_manager.add_account(token)
    logger.info(f"Successfully initialized {len(account_manager.accounts)} accounts")

if not account_manager.accounts:
    logger.error("No accounts were initialized - service may not function correctly")
