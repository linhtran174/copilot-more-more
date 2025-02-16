from dotenv import load_dotenv
import os
from typing import List

from copilot_more.logger import logger


def load_config() -> List[str]:
    """Load configuration from environment variables."""
    load_dotenv()

    refresh_tokens = os.getenv("REFRESH_TOKENS", "")
    if not refresh_tokens:
        logger.error("REFRESH_TOKENS environment variable is not set")
        return []

    tokens = [token.strip() for token in refresh_tokens.split(",") if token.strip()]
    logger.info(f"Found {len(tokens)} refresh tokens in environment")

    return tokens


# Load configuration once at module import
refresh_tokens = load_config()
