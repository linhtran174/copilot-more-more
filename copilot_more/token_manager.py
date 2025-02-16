import json
import os
import threading
import time
from typing import Dict, List, Optional

from copilot_more.logger import logger

class TokenInfo:
    def __init__(self, token: str, expires_at: int):
        self.token = token
        self.expires_at = expires_at
        self.rate_limited_until = 0
        self.last_used = 0

    def is_valid(self) -> bool:
        return self.expires_at > time.time() + 300

    def is_rate_limited(self) -> bool:
        return time.time() < self.rate_limited_until

    def mark_rate_limited(self, duration: int = 60):
        self.rate_limited_until = time.time() + duration

    def to_dict(self) -> Dict:
        return {
            "token": self.token,
            "expires_at": self.expires_at,
            "rate_limited_until": self.rate_limited_until,
            "last_used": self.last_used
        }

class TokenManager:
    def __init__(self):
        self.tokens: List[TokenInfo] = []
        self.current_index = 0
        self.lock = threading.Lock()

    def add_token(self, token_data: Dict):
        token = TokenInfo(token_data["token"], token_data["expires_at"])
        with self.lock:
            self.tokens.append(token)

    def get_next_valid_token(self) -> Optional[TokenInfo]:
        if not self.tokens:
            return None

        with self.lock:
            start_index = self.current_index
            while True:
                self.current_index = (self.current_index + 1) % len(self.tokens)
                current_token = self.tokens[self.current_index]

                if not current_token.is_rate_limited() and current_token.is_valid():
                    current_token.last_used = time.time()
                    return current_token

                if self.current_index == start_index:
                    # We've checked all tokens and none are valid
                    return None

    def handle_rate_limit(self, token: str):
        """Mark a token as rate-limited when a 429 response is received."""
        with self.lock:
            for t in self.tokens:
                if t.token == token:
                    t.mark_rate_limited()
                    break

    def load_tokens_from_file(self, file_path: str = "tokens.json"):
        """Load tokens from a JSON file."""
        try:
            if not os.path.exists(file_path):
                logger.warning(f"Tokens file {file_path} not found")
                return

            with open(file_path, 'r') as f:
                token_data = json.load(f)
                for token in token_data:
                    self.add_token(token)
                logger.info(f"Loaded {len(token_data)} tokens")
        except Exception as e:
            logger.error(f"Error loading tokens: {str(e)}")

# Global token manager instance
token_manager = TokenManager()
