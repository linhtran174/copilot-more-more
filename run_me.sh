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
API Information
=====================================
Provider: GitHub Copilot
API URL: https://api.individual.githubcopilot.com
Base Endpoints:
  - Chat Completions: /chat/completions
  - Models: /models
Default Port: 15432

Available Models:
  - `gpt-4o`
  - `claude-3.5-sonnet`
  - `o1`
  - `o1-mini`

Authentication:
  - Bearer token required (automatically managed)
  - Multiple tokens supported with round-robin rotation
  - Automatic rate limit handling

Usage:
  1. Set REFRESH_TOKEN in .env file
  2. Server runs on http://localhost:15432
  3. Use standard OpenAI API format for requests
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
if [ ! -f .env ]; then
    echo "No .env file found. Running init.sh to generate tokens..."
    bash init.sh
fi

# Install dependencies
echo "Installing dependencies..."
poetry install

# Print API information
print_api_info

# Start the server
echo "Starting the server..."
poetry run uvicorn copilot_more.server:app --port 15432
