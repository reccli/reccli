"""
Config - Manage API keys and user settings
"""

import json
from pathlib import Path
from typing import Optional, Dict


class Config:
    """Manage RecCli configuration"""

    def __init__(self):
        self.config_dir = Path.home() / 'reccli'
        self.config_file = self.config_dir / 'config.json'
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data = self.load()

    def load(self) -> Dict:
        """Load configuration from file"""
        default_config = {
            'api_keys': {
                'anthropic': None,
                'openai': None,
            },
            'default_model': 'claude',
            'sessions_dir': str(Path.home() / 'reccli' / 'devsession'),
            'auto_reason': False,
            'mmc': False,
            'session_signal': True,
            'expanded_search': False,
        }

        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                loaded = json.load(f)
                # Merge with defaults to handle missing keys
                default_config.update(loaded)
                # Ensure api_keys exists
                if 'api_keys' not in default_config:
                    default_config['api_keys'] = {'anthropic': None, 'openai': None}
                return default_config

        # Return default config
        return default_config

    def save(self):
        """Save configuration to file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.data, f, indent=2)

    def set_api_key(self, provider: str, key: str):
        """Set API key for provider"""
        if provider not in ['anthropic', 'openai']:
            raise ValueError(f"Unknown provider: {provider}")

        self.data['api_keys'][provider] = key
        self.save()

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for provider. Checks environment variables first, then config file."""
        import os
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }
        env_var = env_map.get(provider)
        if env_var:
            env_val = os.environ.get(env_var)
            if env_val:
                return env_val
        return self.data['api_keys'].get(provider)

    def set_default_model(self, model: str):
        """Set default model"""
        self.data['default_model'] = model
        self.save()

    def get_default_model(self) -> str:
        """Get default model"""
        return self.data.get('default_model', 'claude')

    def get_sessions_dir(self) -> Path:
        """Get sessions directory"""
        try:
            from ..project.devproject import default_devsession_dir
            sessions_dir = default_devsession_dir(Path.cwd())
        except Exception:
            sessions_dir = Path(self.data.get('sessions_dir', Path.home() / 'reccli' / 'devsession'))
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return sessions_dir

    # ------------------------------------------------------------------
    # License / Pro gating
    # ------------------------------------------------------------------

    _VALIDATE_URL = "https://reccli.dev/api/validate-license"
    _CACHE_FILE_NAME = ".license_cache.json"
    _CACHE_TTL_SECONDS = 86400  # re-validate once per day

    def is_pro(self) -> bool:
        """Check if the user has an active Pro license.

        Validation order:
        1. Local cache (valid for 24h)
        2. Remote validation (caches result on success)
        3. Falls back to local-only if offline
        """
        # No license key at all → free tier
        license_key = self.data.get("license_key")
        if not license_key:
            return False

        # Check local cache first
        cache = self._read_license_cache()
        if cache is not None:
            return cache

        # Try remote validation
        try:
            result = self._validate_remote(license_key)
            self._write_license_cache(result)
            return result
        except Exception:
            # Offline / server down — check subscription_active flag as fallback
            return bool(self.data.get("subscription_active", False))

    def _cache_path(self) -> Path:
        return self.config_dir / self._CACHE_FILE_NAME

    def _read_license_cache(self) -> Optional[bool]:
        """Read cached validation result. Returns None if expired or missing."""
        import time
        cache_file = self._cache_path()
        if not cache_file.exists():
            return None
        try:
            with open(cache_file) as f:
                cache = json.load(f)
            if time.time() - cache.get("validated_at", 0) < self._CACHE_TTL_SECONDS:
                return cache.get("valid", False)
        except Exception:
            pass
        return None

    def _write_license_cache(self, valid: bool) -> None:
        import time
        try:
            with open(self._cache_path(), "w") as f:
                json.dump({"valid": valid, "validated_at": time.time()}, f)
        except Exception:
            pass

    def _validate_remote(self, license_key: str) -> bool:
        """Validate license against the remote server."""
        import urllib.request
        device_id = self.data.get("device_id", "")
        payload = json.dumps({
            "license_key": license_key,
            "device_id": device_id,
        }).encode()
        req = urllib.request.Request(
            self._VALIDATE_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read())
                valid = result.get("valid", False)
                # Persist subscription state locally
                self.data["subscription_active"] = valid
                self.save()
                return valid
        except Exception:
            raise  # Let caller handle offline case

    def activate_license(self, license_key: str) -> str:
        """Set and validate a license key. Returns status message."""
        self.data["license_key"] = license_key
        self.save()
        try:
            valid = self._validate_remote(license_key)
            self._write_license_cache(valid)
            if valid:
                return "License activated. Pro features unlocked."
            else:
                return "License key is invalid or expired."
        except Exception:
            return "Could not reach license server. Key saved — will validate next time."


# Module-level memoization of providers that have failed during this process.
# Prevents wasted retries against credit-depleted or auth-broken accounts.
_BROKEN_PROVIDERS: set = set()


def _provider_for_model(model: Optional[str]) -> Optional[str]:
    """Infer the configured provider from a model alias/name."""
    model_lower = (model or "").strip().lower()
    if model_lower.startswith("claude"):
        return "anthropic"
    if model_lower.startswith("gpt"):
        return "openai"
    return None


def _default_model_for_provider(provider: str) -> str:
    if provider == "anthropic":
        return "claude-sonnet-4-6"
    if provider == "openai":
        return "gpt-5.4"
    raise RuntimeError(f"Unsupported LLM provider: {provider}")


def mark_provider_broken(provider: str) -> None:
    """Mark a provider unusable for the rest of this process so future
    select_llm_client() calls skip it. Call this after a failed LLM API
    call when the failure is provider-fatal (auth, credit, permission)
    rather than transient (rate-limit, network)."""
    _BROKEN_PROVIDERS.add(provider)


# A provider-fatal failure will recur on every retry; a transient one will not.
# Calling mark_provider_broken() on a 429 disabled the provider for the rest of
# the process, and with a single provider configured that made the retry path
# unreachable: the first rate limit of the session ended summarization entirely.
_FATAL_MARKERS = (
    "401", "403", "402",
    "authentication", "invalid api key", "invalid x-api-key", "no api key",
    "permission", "unauthorized", "credit balance", "billing", "account is not active",
)
_TRANSIENT_MARKERS = (
    "429", "500", "502", "503", "504",
    "rate limit", "rate_limit", "overloaded", "capacity",
    "timeout", "timed out", "connection", "temporarily unavailable", "try again",
)


def is_provider_fatal(error_text: Optional[str]) -> bool:
    """True only when a failure will recur on every retry with this provider.

    Unknown failures are treated as NOT fatal on purpose. Wrongly disabling a
    working provider costs the whole session's summarization, while wrongly
    retrying a genuinely dead one costs one extra call.
    """
    text = (error_text or "").lower()
    if not text:
        return False
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return False
    return any(marker in text for marker in _FATAL_MARKERS)


def select_llm_client(prefer: str = "auto"):
    """Pick an LLM client based on configured keys + preference.

    Resolution priority:
      1. RECCLI_LLM_PROVIDER env var (anthropic | openai) — explicit override
      2. The `prefer` argument, when it names a provider
      3. Provider implied by the configured default_model
      4. Anthropic if its key is configured
      5. OpenAI if its key is configured

    Skips any provider marked broken via mark_provider_broken().

    Returns (client, default_model, provider_name).
    Raises RuntimeError if no usable provider is available.
    """
    import os

    config = Config()
    default_model = config.get_default_model()
    env_pref = (os.environ.get("RECCLI_LLM_PROVIDER") or "").strip().lower()
    arg_pref = (prefer or "auto").strip().lower()

    def _model_for_provider(provider: str) -> str:
        if _provider_for_model(default_model) == provider:
            return default_model
        return _default_model_for_provider(provider)

    def _try(provider: str):
        if provider in _BROKEN_PROVIDERS:
            return None
        if provider == "anthropic":
            key = config.get_api_key("anthropic")
            if not key:
                return None
            try:
                import anthropic
            except ImportError:
                return None
            return anthropic.Anthropic(api_key=key), _model_for_provider(provider), "anthropic"
        if provider == "openai":
            key = config.get_api_key("openai")
            if not key:
                return None
            try:
                from openai import OpenAI
            except ImportError:
                return None
            return OpenAI(api_key=key), _model_for_provider(provider), "openai"
        return None

    explicit_pref = env_pref or (arg_pref if arg_pref not in {"", "auto"} else "")
    if explicit_pref:
        if explicit_pref not in {"anthropic", "openai"}:
            raise RuntimeError(
                f"Unsupported LLM provider '{explicit_pref}'. "
                "Supported providers: anthropic, openai."
            )
        result = _try(explicit_pref)
        if result:
            return result
        raise RuntimeError(
            f"Preferred LLM provider '{explicit_pref}' is not available "
            f"(no key configured, package missing, or marked broken this run)."
        )

    provider_order = []
    default_provider = _provider_for_model(default_model)
    if default_provider:
        provider_order.append(default_provider)
    for provider in ("anthropic", "openai"):
        if provider not in provider_order:
            provider_order.append(provider)

    for provider in provider_order:
        result = _try(provider)
        if result:
            return result

    raise RuntimeError(
        "No LLM provider available. Configure ANTHROPIC_API_KEY or OPENAI_API_KEY "
        "via 'reccli config' or env vars."
    )
