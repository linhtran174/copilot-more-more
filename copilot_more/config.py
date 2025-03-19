import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union

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

@dataclass
class ProviderConfig:
    """Base class for provider configurations."""
    type: str = ""
    enabled: bool = True
    priority: int = 100
    rate_limit_windows: List[RateLimitWindow] = field(default_factory=lambda: DEFAULT_RATE_LIMIT_WINDOWS.copy())

@dataclass
class GithubCopilotProviderConfig(ProviderConfig):
    """Configuration for GitHub Copilot provider."""
    accounts: List[AccountConfig] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.type:
            self.type = "github-copilot"

# Note: We're using a separate class instead of inheritance to avoid dataclass field order issues
@dataclass
class OpenAICompatibleProviderConfig:
    """Configuration for OpenAI compatible provider."""
    base_url: str
    api_key: str
    type: str = "openai-compatible"
    enabled: bool = True
    priority: int = 100
    rate_limit_windows: List[RateLimitWindow] = field(default_factory=lambda: DEFAULT_RATE_LIMIT_WINDOWS.copy())
    model_mapping: Dict[str, str] = field(default_factory=dict)

@dataclass
class MasterKeyConfig:
    """Configuration for a master API key."""
    user_id: str
    enabled: bool = True
    description: str = ""

@dataclass
class ApiKeyConfig:
    """Configuration for API keys."""
    master_keys: List[MasterKeyConfig] = field(default_factory=list)

class Config:
    """Main configuration class."""
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.accounts: List[AccountConfig] = []
        self.providers: List[Union[GithubCopilotProviderConfig, OpenAICompatibleProviderConfig]] = []
        self.request_timeout: int = 60  # default timeout
        self.record_traffic: bool = False  # default record traffic setting
        self.master_key: Optional[str] = None
        self.system_models: Dict[str, Dict[str, Any]] = {}  # System-provided models configuration
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

            # Check if providers section exists in config
            providers_data = config_data.get('providers', [])
            if providers_data:
                # New provider-based configuration
                self._load_providers(providers_data, rate_limit_windows)
            else:
                # Legacy configuration - create a GitHub Copilot provider from accounts
                self._load_legacy_config(config_data, rate_limit_windows)

            # Load other settings
            self.request_timeout = config_data.get('request_timeout', 60)
            self.record_traffic = config_data.get('record_traffic', False)
            self.master_key = config_data.get('master_key')
            self.system_models = config_data.get('system_models', {})

            logger.info(f"Successfully loaded configuration with {len(self.providers)} providers and {len(self.system_models)} system models")

        except Exception as e:
            logger.error(f"Error loading config: {e}")
            raise
            
    def _load_providers(self, providers_data: List[Dict[str, Any]], default_rate_limits: List[RateLimitWindow]) -> None:
        """Load provider configurations from the providers section."""
        self.providers = []
        
        for provider_data in providers_data:
            provider_type = provider_data.get('type', '').lower()
            
            # Parse rate limits for this provider
            provider_rate_limits = default_rate_limits
            if 'rate_limits' in provider_data:
                provider_rate_limits = [
                    RateLimitWindow(
                        duration=window['duration'],
                        max_requests=window['max_requests']
                    )
                    for window in provider_data['rate_limits']
                ]
            
            if provider_type == 'github-copilot':
                # Create GitHub Copilot provider config
                accounts = []
                
                # Parse accounts for GitHub Copilot provider
                for account_data in provider_data.get('accounts', []):
                    proxy_config = None
                    if "proxy" in account_data:
                        proxy = account_data["proxy"]
                        proxy_config = ProxyConfig(
                            host=proxy["host"],
                            port=proxy["port"],
                            username=proxy.get("username"),
                            password=proxy.get("password")
                        )
                        
                    # Get account-specific rate limits if defined, otherwise use provider's
                    account_rate_limits = provider_rate_limits
                    if "rate_limits" in account_data:
                        account_rate_limits = [
                            RateLimitWindow(
                                duration=window['duration'],
                                max_requests=window['max_requests']
                            )
                            for window in account_data['rate_limits']
                        ]
                        
                    accounts.append(
                        AccountConfig(
                            refresh_token=account_data["token"],
                            username=account_data["id"],
                            proxy=proxy_config,
                            rate_limit_windows=account_rate_limits
                        )
                    )
                
                # Create and add the provider config
                self.providers.append(
                    GithubCopilotProviderConfig(
                        type=provider_type,
                        enabled=provider_data.get('enabled', True),
                        priority=provider_data.get('priority', 1),  # Default priority 1 for GitHub Copilot
                        rate_limit_windows=provider_rate_limits,
                        accounts=accounts
                    )
                )
                
            elif provider_type == 'openai-compatible':
                # Create OpenAI compatible provider config
                self.providers.append(
                    OpenAICompatibleProviderConfig(
                        base_url=provider_data.get('base_url', 'https://api.openai.com/v1'),
                        api_key=provider_data.get('api_key', ''),
                        type=provider_type,
                        enabled=provider_data.get('enabled', True),
                        priority=provider_data.get('priority', 2),  # Default priority 2 for OpenAI compatible
                        rate_limit_windows=provider_rate_limits,
                        model_mapping=provider_data.get('model_mapping', {})
                    )
                )
    
    def _load_legacy_config(self, config_data: Dict[str, Any], rate_limit_windows: List[RateLimitWindow]) -> None:
        """Load legacy configuration format and convert to provider-based configuration."""
        # Load accounts configuration from legacy format
        accounts_data = config_data.get('accounts', [])
        accounts = []
        print(f"Acc data ${accounts_data}");
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

            accounts.append(
                AccountConfig(
                    refresh_token=account_data["token"],
                    username=account_data["id"],
                    proxy=proxy_config,
                    rate_limit_windows=account_rate_limits or rate_limit_windows
                )
            )
        
        # Create a GitHub Copilot provider with the accounts
        if accounts:
            self.providers.append(
                GithubCopilotProviderConfig(
                    type="github-copilot",
                    enabled=True,
                    priority=1,  # Default priority 1 for GitHub Copilot
                    rate_limit_windows=rate_limit_windows,
                    accounts=accounts
                )
            )
            
        # Also store accounts in accounts field for backward compatibility
        self.accounts = accounts
        
        logger.info(f"Converted {len(accounts)} accounts to GitHub Copilot provider")

# Create global config instance
config = Config()

# Export commonly used values
account_configs = config.accounts
provider_configs = config.providers
request_timeout = config.request_timeout
record_traffic = config.record_traffic
