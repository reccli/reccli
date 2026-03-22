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
        """Get API key for provider"""
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
