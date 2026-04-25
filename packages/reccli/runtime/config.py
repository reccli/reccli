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
