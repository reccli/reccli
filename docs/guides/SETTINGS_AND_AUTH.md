# Settings and Authentication

## Overview

RecCli requires API keys for AI-powered features (session summarization, embeddings, project initialization). This document outlines the authentication strategy and settings storage.

---

## Authentication Strategy

### MVP: API Key Authentication

For the initial release, RecCli uses API keys for simplicity and reliability:

```bash
# First-time setup
$ reccli config set anthropic-api-key sk-ant-...
✓ Anthropic API key saved

$ reccli config set openai-api-key sk-...
✓ OpenAI API key saved
```

**Why API keys for MVP:**
- ✅ Reliable and officially supported
- ✅ Clear documentation from providers
- ✅ Works across all platforms
- ✅ No dependency on browser/OAuth infrastructure
- ✅ Get MVP shipped faster

**Cost transparency:**
- Anthropic Claude Sonnet 4.5: ~$0.003 per 1K tokens (input)
- OpenAI text-embedding-3-small: ~$0.0001 per 1K tokens
- Typical session: ~$0.10-0.50 total cost

### Future: OAuth Flow (v1.1+)

Request official OAuth support from Anthropic:

```bash
# Future desired flow
$ reccli auth login

Opening browser for authentication...
✓ Logged in as will@example.com (Claude Pro)
✓ Session saved
```

**Benefits of OAuth:**
- ✅ No separate API key required
- ✅ Use existing Claude Pro/Max subscription
- ✅ Familiar auth pattern (like GitHub CLI)
- ✅ Lower friction for users

**Required from Anthropic:**
- OAuth 2.0 flow for CLI applications
- API access for Pro/Max subscribers (or discounted API credits)
- Session token that works with API endpoints

---

## Settings Storage

### Location

```
~/.reccli/
├── config.json          # User settings (encrypted API keys)
├── projects.json        # Recent projects cache
└── session.json         # Future: OAuth session token
```

**Why home directory:**
- ✅ Persistent across projects
- ✅ Standard location for CLI tools
- ✅ User-specific (multi-user systems)

### config.json Structure

```json
{
  "version": "1.0.0",
  "api_keys": {
    "anthropic": "ENCRYPTED:...",
    "openai": "ENCRYPTED:..."
  },
  "preferences": {
    "default_storage": "project_root",
    "auto_export_on_stop": true,
    "generate_embeddings": true,
    "preemptive_compaction": 190000,
    "default_embedding_model": "text-embedding-3-small",
    "default_llm_model": "claude-sonnet-4.5"
  },
  "storage": {
    "default_location": "project_root",
    "custom_path": null
  }
}
```

**API Key Encryption:**
- Keys encrypted at rest using user's system keychain
- macOS: Keychain Access
- Linux: libsecret / gnome-keyring
- Windows: Windows Credential Manager

```python
# Implementation example
from keyring import set_password, get_password

def save_api_key(service, key):
    """Save API key to system keychain"""
    set_password('reccli', service, key)

def load_api_key(service):
    """Load API key from system keychain"""
    return get_password('reccli', service)
```

### projects.json Structure

```json
{
  "version": "1.0.0",
  "projects": [
    {
      "id": "reccli-001",
      "name": "RecCli",
      "path": "/Users/will/projects/RecCli",
      "last_accessed": "2024-10-27T18:30:00Z",
      "session_count": 12,
      "has_devproject": true
    },
    {
      "id": "myapp-002",
      "name": "MyApp",
      "path": "/Users/will/projects/myapp",
      "last_accessed": "2024-10-26T14:20:00Z",
      "session_count": 5,
      "has_devproject": true
    }
  ],
  "max_recent": 10
}
```

**Cache updates:**
- Add project on first session
- Update last_accessed on each session
- Increment session_count on each session
- Remove projects that no longer exist

---

## Configuration Commands

### Setup Commands

```bash
# API Keys
reccli config set anthropic-api-key <key>
reccli config set openai-api-key <key>

# View current settings (keys masked)
reccli config list

# Test API keys
reccli config test

# Remove API key
reccli config unset anthropic-api-key
```

### Preference Commands

```bash
# Storage location
reccli config set storage project_root  # Default
reccli config set storage home          # ~/.reccli/sessions/
reccli config set storage custom /path/to/sessions

# Auto-export
reccli config set auto-export true      # Export on STOP
reccli config set auto-export false     # Manual export only

# Embeddings
reccli config set embeddings true       # Generate vectors
reccli config set embeddings false      # Skip vectors (faster, smaller)

# Compaction threshold
reccli config set compaction-threshold 190000
```

---

## First-Time Setup Flow

### Scenario 1: No API Keys

```bash
$ reccli

⚠️  No API keys configured. RecCli needs API keys for AI features.

📦 Basic recording works without API keys, but you'll miss:
   • Automatic session summaries
   • Smart context continuation
   • Project overview generation
   • Vector-based search

🔑 Get API keys:
   • Anthropic: https://console.anthropic.com/keys
   • OpenAI: https://platform.openai.com/api-keys

💰 Cost: ~$0.10-0.50 per session

Would you like to:
  [1] Configure API keys now
  [2] Continue without AI features
  [3] Learn more

Choice:
```

### Scenario 2: Setup Wizard

```bash
$ reccli config setup

🚀 RecCli Setup Wizard

Step 1/3: Anthropic API Key
Get your key: https://console.anthropic.com/keys
Enter key (or press Enter to skip): sk-ant-...
✓ Anthropic API key saved and tested

Step 2/3: OpenAI API Key
Get your key: https://platform.openai.com/api-keys
Enter key (or press Enter to skip): sk-...
✓ OpenAI API key saved and tested

Step 3/3: Preferences
Storage location:
  [1] Project root (recommended - sessions travel with project)
  [2] Home directory (centralized - sessions in ~/.reccli/sessions/)
  [3] Custom location

Choice: 1
✓ Storage set to project root

✅ Setup complete! Run 'reccli' to start recording.
```

### Scenario 3: Partial Setup

```bash
$ reccli

✓ Anthropic API key configured
⚠️  No OpenAI API key found

RecCli will use Anthropic for embeddings (more expensive).
Add OpenAI key to save ~70% on embedding costs.

Configure now? [y/N]:
```

---

## Error Handling

### Missing API Keys

```python
def require_api_keys():
    """Check if required API keys are present"""
    anthropic_key = load_api_key('anthropic')
    openai_key = load_api_key('openai')

    if not anthropic_key:
        print("⚠️  No Anthropic API key configured")
        print("Run: reccli config set anthropic-api-key <key>")
        return False

    if not openai_key:
        print("⚠️  No OpenAI API key (will use Anthropic for embeddings)")
        print("This is more expensive. Add OpenAI key to save ~70%.")
        # Don't fail - can continue with Anthropic only

    return True
```

### Invalid API Keys

```python
def test_api_keys():
    """Test API keys with minimal requests"""
    results = {
        'anthropic': False,
        'openai': False
    }

    # Test Anthropic
    try:
        client = anthropic.Anthropic(api_key=load_api_key('anthropic'))
        response = client.messages.create(
            model="claude-sonnet-4.5-20250929",
            max_tokens=10,
            messages=[{"role": "user", "content": "test"}]
        )
        results['anthropic'] = True
    except Exception as e:
        print(f"❌ Anthropic API key invalid: {e}")

    # Test OpenAI
    try:
        client = openai.OpenAI(api_key=load_api_key('openai'))
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input="test"
        )
        results['openai'] = True
    except Exception as e:
        print(f"❌ OpenAI API key invalid: {e}")

    return results
```

### Network Failures

```python
def api_call_with_retry(func, max_retries=3):
    """Retry API calls with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return func()
        except NetworkError as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                print(f"⚠️  Network error, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise Exception(f"Failed after {max_retries} attempts: {e}")
```

---

## Security Considerations

### API Key Storage

**DO:**
- ✅ Use system keychain (keyring library)
- ✅ Encrypt at rest
- ✅ Restrict file permissions (chmod 600)
- ✅ Never log full API keys
- ✅ Mask in UI (sk-ant-...abc123)

**DON'T:**
- ❌ Store in plaintext
- ❌ Commit to git
- ❌ Share in error messages
- ❌ Include in exported sessions

### .devsession Files

**.devsession files may contain sensitive information:**
- API endpoints and URLs
- Code snippets
- Project structure
- File paths
- Internal architecture details

**Privacy by default:**
- Auto-gitignore `.devproject` and `.devsessions/`
- Clear documentation on opting in to version control
- Warning on first git add of .devsession files

---

## Future Auth Enhancements

### Planned for v1.1+

1. **OAuth Flow** (if Anthropic supports)
   - Browser-based authentication
   - Use existing Claude Pro/Max subscription
   - No separate API key needed

2. **Local Model Support** (free alternative)
   - Ollama integration for summaries
   - Local embeddings (sentence-transformers)
   - No API keys required (slower, lower quality)

3. **Team Features**
   - Shared project API keys
   - Organization-level authentication
   - Usage tracking per developer

4. **Cost Tracking**
   - Per-session cost breakdown
   - Monthly usage reports
   - Budget alerts

---

## Implementation Checklist

### MVP (v1.0)

- [ ] Implement keyring-based API key storage
- [ ] Create config.json structure
- [ ] Build setup wizard (`reccli config setup`)
- [ ] Add API key test functionality
- [ ] Create projects.json cache
- [ ] Handle missing API keys gracefully
- [ ] Add error handling for invalid keys
- [ ] Document API key acquisition process
- [ ] Add cost transparency messaging

### Post-MVP (v1.1+)

- [ ] Request OAuth support from Anthropic
- [ ] Implement OAuth flow (if available)
- [ ] Add local model fallbacks (Ollama)
- [ ] Build cost tracking dashboard
- [ ] Add team/organization features
- [ ] Explore ChatGPT Plus integration
