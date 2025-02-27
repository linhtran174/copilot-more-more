import json
import os
from dataclasses import dataclass
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
class AccountConfig:
    """Configuration for a GitHub Copilot account."""
    refresh_token: str
    username: str
    proxy: Optional[ProxyConfig] = None

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
                self.accounts.append(
                    AccountConfig(
                        refresh_token=account_data["token"],
                        username=account_data["id"],
                        proxy=proxy_config
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
