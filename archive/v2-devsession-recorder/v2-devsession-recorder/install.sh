#!/bin/bash

echo "Installing RecCli with TypeScript UI..."

# Build TypeScript UI
echo "Building TypeScript UI..."
cd ui
npm install
npm run build
cd ..

# Make reccli executable
chmod +x bin/reccli

# Create symlink in user bin
mkdir -p ~/bin
ln -sf "$(pwd)/bin/reccli" ~/bin/reccli

# Check if ~/bin is in PATH
if [[ ":$PATH:" != *":$HOME/bin:"* ]]; then
    echo ""
    echo "⚠️  ~/bin is not in your PATH"
    echo "Add this line to your ~/.zshrc or ~/.bashrc:"
    echo '    export PATH="$HOME/bin:$PATH"'
    echo ""
    echo "Then reload your shell or run:"
    echo "    source ~/.zshrc"
else
    echo "✅ RecCli installed successfully!"
fi

echo ""
echo "You can now use RecCli in a new terminal with:"
echo "    reccli          # Start chat with TypeScript UI"
echo "    reccli chat     # Start chat"
echo "    reccli record   # Record terminal session"
echo ""