import time
from dataclasses import dataclass
from typing import Dict, Optional
import secrets
import threading
from copilot_more.logger import logger

@dataclass
class ApiKeyInfo:
    """Represents an API key with usage tracking."""
    key: str
    user_id: str
    created_at: int
    credits: float
    total_tokens_used: int
    enabled: bool = True

    def has_sufficient_credits(self, estimated_tokens: int = 1000) -> bool:
        """Check if key has sufficient credits for the estimated token usage."""
        # Assuming 1 credit = 1000 tokens, and we want to prevent going negative
        estimated_cost = estimated_tokens / 1000
        return self.credits >= estimated_cost and self.enabled

    def deduct_tokens(self, tokens_used: int) -> bool:
        """Deduct tokens from credits and update total usage."""
        credit_cost = tokens_used / 500000  # $2 / 1M tokens
        if self.credits >= credit_cost:
            self.credits -= credit_cost
            self.total_tokens_used += tokens_used
            return True
        return False

class ApiKeyManager:
    """Manages API keys, credits, and usage tracking."""

    def __init__(self):
        self.api_keys: Dict[str, ApiKeyInfo] = {}
        self.lock = threading.Lock()

    def create_api_key(self, user_id: str, initial_credits: float = 0.0) -> str:
        """Create a new API key for a user with optional initial credits."""
        with self.lock:
            api_key = f"cm-{secrets.token_urlsafe(32)}"
            self.api_keys[api_key] = ApiKeyInfo(
                key=api_key,
                user_id=user_id,
                created_at=int(time.time()),
                credits=initial_credits,
                total_tokens_used=0
            )
            logger.info(f"Created new API key for user {user_id}")
            return api_key

    def get_key_info(self, api_key: str) -> Optional[ApiKeyInfo]:
        """Get information about an API key."""
        return self.api_keys.get(api_key)

    def add_credits(self, api_key: str, amount: float) -> bool:
        """Add credits to an API key."""
        with self.lock:
            if key_info := self.api_keys.get(api_key):
                key_info.credits += amount
                logger.info(f"Added {amount} credits to API key {api_key}")
                return True
            return False

    def validate_key(self, api_key: str, estimated_tokens: int = 1000) -> bool:
        """Validate an API key and check if it has sufficient credits."""
        if key_info := self.api_keys.get(api_key):
            return key_info.has_sufficient_credits(estimated_tokens)
        return False

    def deduct_tokens(self, api_key: str, tokens_used: int) -> bool:
        """Deduct tokens from an API key's credits."""
        with self.lock:
            if key_info := self.api_keys.get(api_key):
                return key_info.deduct_tokens(tokens_used)
            return False

    def disable_key(self, api_key: str) -> bool:
        """Disable an API key."""
        with self.lock:
            if key_info := self.api_keys.get(api_key):
                key_info.enabled = False
                logger.info(f"Disabled API key {api_key}")
                return True
            return False

    def enable_key(self, api_key: str) -> bool:
        """Enable a disabled API key."""
        with self.lock:
            if key_info := self.api_keys.get(api_key):
                key_info.enabled = True
                logger.info(f"Enabled API key {api_key}")
                return True
            return False

# Global API key manager instance
api_key_manager = ApiKeyManager()