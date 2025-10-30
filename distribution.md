# RecCli Distribution Strategy

## Distribution Options Analysis

### 1. Homebrew (Recommended - Best for developers)
- ✅ Easy install/update (`brew install reccli`)
- ✅ Can install background service (LaunchAgent)
- ✅ Free to distribute
- ✅ Your target audience (devs) already use it
- ✅ Standard for dev tools

### 2. Mac App Store
- ✅ Yes, same $99/year Apple Developer account covers iOS + macOS
- ❌ Sandboxing restrictions (harder to control Terminal windows)
- ❌ Long review process
- ❌ Can't easily auto-launch with Terminal
- ⚠️ Better for consumer apps, not dev tools

### 3. Direct Download + Installer
- ✅ Full control
- ✅ Can set up LaunchAgent automatically
- ❌ Manual updates
- ⚠️ Less discoverable

## Recommended Approach: Homebrew + LaunchAgent

For a dev tool like RecCli, the best distribution strategy is:

1. **Distribute via Homebrew** (homebrew-cask for GUI apps)
2. **Background watcher process** that monitors for new Terminal windows
3. **LaunchAgent** that starts on login

### Benefits
- ✅ Auto-launch on system startup
- ✅ Auto-detect new terminals
- ✅ Easy updates (`brew upgrade reccli`)
- ✅ Standard pattern for dev tools (like docker, postgres, etc.)
- ✅ Familiar workflow for target audience
- ✅ No App Store restrictions
- ✅ Free to distribute

### Implementation Plan

#### Background Watcher Features
- Monitor Terminal.app for new windows
- Auto-launch popups for new terminals
- Clean up popups when terminals close
- Run as a lightweight daemon
- Low resource usage

#### Homebrew Formula Structure
```ruby
cask "reccli" do
  version "1.0.0"

  # Installation
  app "RecCli.app"

  # LaunchAgent for background watcher
  launchagent "com.reccli.watcher.plist"
end
```

#### LaunchAgent Configuration
- Runs on login
- Monitors Terminal.app
- Manages popup lifecycle
- Minimal system impact

### Alternative/Complementary Channels

**GitHub Releases**
- Direct download option
- Installation script included
- Good for early adopters before Homebrew

**Product Hunt / Hacker News**
- Launch announcement
- Developer community awareness
- Feedback gathering

### Timeline

1. **Phase 1: Development** (Current)
   - Implement background watcher
   - Test LaunchAgent integration
   - Prepare install/uninstall scripts

2. **Phase 2: Soft Launch**
   - GitHub releases
   - Direct download + install script
   - Early user feedback

3. **Phase 3: Homebrew**
   - Submit to homebrew-cask
   - Official distribution channel
   - Update documentation

4. **Phase 4: (Optional) App Store**
   - Consider if user demand warrants it
   - May require significant refactoring for sandbox
   - Evaluate cost/benefit

## Cost Analysis

- **Homebrew**: $0
- **GitHub Releases**: $0
- **Direct Website**: ~$10-20/year (domain + hosting)
- **Apple Developer Account**: $99/year (if you already have for iOS, covers macOS too)
- **App Store** (optional): Included in Developer account, but requires review process

## Conclusion

**Primary distribution: Homebrew with LaunchAgent**

This provides the best developer experience, easiest installation, and most reliable auto-update mechanism for a CLI/GUI developer tool targeting macOS Terminal users.
