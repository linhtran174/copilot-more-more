#!/bin/bash

# Function to check if poetry is available in PATH or home directory
check_poetry() {
    # Check in PATH
    if command -v poetry &> /dev/null; then
        return 0
    fi
    
    # Check in common poetry install locations
    if [ -f "$HOME/.local/bin/poetry" ]; then
        export PATH="$HOME/.local/bin:$PATH"
        return 0
    elif [ -f "$HOME/.poetry/bin/poetry" ]; then
        export PATH="$HOME/.poetry/bin:$PATH"
        return 0
    fi
    
    return 1
}

# Print API information
print_api_info() {
    echo "=====================================
Popular Models:
  - gpt-4o
  - claude-3.5-sonnet
  - claude-3.7-sonnet
  - o1
  - o1-mini

Usage:
  1. Check config.json
  2. Chat completion API: http://localhost:15432/chat/completions
  3. Model API: http://localhost:15432/models
=====================================
"
}

# Install poetry if not found
if ! check_poetry; then
    echo "Poetry is not installed. Installing poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
    
    # Add poetry to PATH for current session
    export PATH="$HOME/.local/bin:$PATH"
    
    # Verify installation
    if ! check_poetry; then
        echo "Failed to install poetry. Please install it manually."
        exit 1
    fi
fi

# Check if .env file exists
if [ ! -f config.json ]; then
    echo "No config.json file found. Running init.sh to generate tokens..."
    bash init.sh
fi

# Install dependencies
echo "Installing dependencies..."
poetry lock
poetry install

# Print API information
print_api_info

# Start the server
if [ "$1" == "--debug" ]; then
    echo "Starting server in debug mode..."
    poetry run python -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m uvicorn copilot_more.server:app --port 15432 --reload
else
    echo "Starting the server..."
    poetry run uvicorn copilot_more.server:app --port 15432
fi
