import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from copilot_more.logger import logger

@dataclass
class ProxyConfig:
    """Configuration for a SOCKS5 proxy."""
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

@dataclass
class RateLimitWindow:
    """Configuration for a rate limit window."""
    duration: int  # Duration in seconds
    max_requests: int  # Maximum number of requests allowed in this window

DEFAULT_RATE_LIMIT_WINDOWS = [
    RateLimitWindow(duration=10, max_requests=2),   # 10 sec window
    RateLimitWindow(duration=60, max_requests=10),  # 1 min window
    RateLimitWindow(duration=3600, max_requests=40) # 1 hour window
]

@dataclass
class AccountConfig:
    """Configuration for a GitHub Copilot account."""
    refresh_token: str
    username: str
    proxy: Optional[ProxyConfig] = None
    rate_limit_windows: List[RateLimitWindow] = field(default_factory=lambda: DEFAULT_RATE_LIMIT_WINDOWS.copy())

class Config:
    """Main configuration class."""
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.accounts: List[AccountConfig] = []
        self.request_timeout: int = 60  # default timeout
        self.record_traffic: bool = False  # default record traffic setting
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from JSON file."""
        try:
            if not os.path.exists(self.config_file):
                logger.error(f"Config file not found: {self.config_file}")
                return

            with open(self.config_file, 'r') as f:
                config_data = json.load(f)

            # Load rate limit windows
            rate_limit_data = config_data.get('rate_limits', [])
            rate_limit_windows = []
            for window in rate_limit_data:
                rate_limit_windows.append(
                    RateLimitWindow(
                        duration=window['duration'],
                        max_requests=window['max_requests']
                    )
                )
            
            # Use default rate limits if none configured
            if not rate_limit_windows:
                rate_limit_windows = DEFAULT_RATE_LIMIT_WINDOWS

            # Load accounts configuration
            accounts_data = config_data.get('accounts', [])
            self.accounts = []
            for account_data in accounts_data:
                proxy_config = None
                if "proxy" in account_data:
                    proxy = account_data["proxy"]
                    proxy_config = ProxyConfig(
                        host=proxy["host"],
                        port=proxy["port"],
                        username=proxy.get("username"),
                        password=proxy.get("password")
                    )
                    
                # Get account-specific rate limits if defined, otherwise use global
                account_rate_limits = None
                if "rate_limits" in account_data:
                    account_rate_limits = [
                        RateLimitWindow(
                            duration=window['duration'],
                            max_requests=window['max_requests']
                        )
                        for window in account_data['rate_limits']
                    ]

                self.accounts.append(
                    AccountConfig(
                        refresh_token=account_data["token"],
                        username=account_data["id"],
                        proxy=proxy_config,
                        rate_limit_windows=account_rate_limits or rate_limit_windows
                    )
                )

            # Load other settings
            self.request_timeout = config_data.get('request_timeout', 60)
            self.record_traffic = config_data.get('record_traffic', False)

            logger.info(f"Successfully loaded configuration with {len(self.accounts)} accounts")

        except Exception as e:
            logger.error(f"Error loading config: {e}")
            raise

# Create global config instance
config = Config()

# Export commonly used values
account_configs = config.accounts
request_timeout = config.request_timeout
record_traffic = config.record_traffic
