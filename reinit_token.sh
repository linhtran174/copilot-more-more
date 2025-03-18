#!/bin/bash

# Client ID for the vscode copilot
client_id="01ab8ac9400c4e429b23"

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed. Please install it to continue."
    exit 1
fi

# Check if config.json exists
if [ ! -f "config.json" ]; then
    echo "Error: config.json file not found."
    exit 1
fi

# Read the accounts from config.json
accounts=$(jq -r '.providers[] | select(.type == "github-copilot") | .accounts[] | .id' config.json)
account_count=$(echo "$accounts" | wc -l)

if [ "$account_count" -eq 0 ]; then
    echo "No accounts found in config.json"
    exit 1
fi

# Display accounts for selection
echo "Available accounts:"
echo "$accounts" | nl

# Prompt user to select an account
echo -e "\nEnter the number of the account to re-initialize token (1-$account_count):"
read account_num

# Validate selection - repeat until valid input received
while true; do
    if [[ "$account_num" =~ ^[0-9]+$ ]] && [ "$account_num" -ge 1 ] && [ "$account_num" -le "$account_count" ]; then
        break
    else
        echo "Invalid selection. Please enter a number between 1 and $account_count:"
        read account_num
    fi
done

# Get the selected account index (0-based for jq)
account_index=$((account_num - 1))

# Get account details
account_id=$(jq -r ".accounts[$account_index].id" config.json)
proxy_host=$(jq -r ".accounts[$account_index].proxy.host // \"\"" config.json)
proxy_port=$(jq -r ".accounts[$account_index].proxy.port // \"\"" config.json)
proxy_username=$(jq -r ".accounts[$account_index].proxy.username // \"\"" config.json)
proxy_password=$(jq -r ".accounts[$account_index].proxy.password // \"\"" config.json)

echo "Re-initializing token for account: $account_id"

# Check if proxy settings are valid
if [ -n "$proxy_host" ] && [ -n "$proxy_port" ] && [ "$proxy_host" != "null" ] && [ "$proxy_port" != "null" ]; then
    echo "Using proxy: $proxy_host:$proxy_port"
    
    # Format proxy for curl
    proxy_auth=""
    if [ -n "$proxy_username" ] && [ -n "$proxy_password" ] && [ "$proxy_username" != "null" ] && [ "$proxy_password" != "null" ]; then
        proxy_auth="$proxy_username:$proxy_password@"
    fi
    
    proxy_url="socks5://$proxy_auth$proxy_host:$proxy_port"
    proxy_cmd="-x $proxy_url"
else
    echo "No valid proxy configuration found for this account."
    echo -n "Do you want to continue without a proxy? (y/n): "
    read use_direct
    
    if [[ "$use_direct" != "y" && "$use_direct" != "Y" ]]; then
        echo "Operation cancelled."
        exit 1
    fi
    
    proxy_cmd=""
fi

# Get device code
echo "Getting device code..."
response=$(curl -s https://github.com/login/device/code \
    -X POST \
    -d "client_id=$client_id&scope=user:email" \
    $proxy_cmd \
    --connect-timeout 30)

if [ $? -ne 0 ]; then
    echo "Error connecting to GitHub. Check your proxy settings and internet connection."
    exit 1
fi

# Extract codes
device_code=$(echo "$response" | grep -oE 'device_code=[^&]+' | cut -d '=' -f 2)
user_code=$(echo "$response" | grep -oE 'user_code=[^&]+' | cut -d '=' -f 2)

if [ -z "$device_code" ] || [ -z "$user_code" ]; then
    echo "Failed to get authentication codes. GitHub response:"
    echo "$response"
    exit 1
fi

# Print instructions for the user
echo -e "\nPlease open https://github.com/login/device/ and enter the following code: $user_code"
echo "Press Enter once you have authorized the application..."
read

# Get the access token
echo "Getting access token..."
response_access_token=$(curl -s https://github.com/login/oauth/access_token \
    -X POST \
    -d "client_id=$client_id&scope=user:email&device_code=$device_code&grant_type=urn:ietf:params:oauth:grant-type:device_code" \
    $proxy_cmd \
    --connect-timeout 30)

if [ $? -ne 0 ]; then
    echo "Error connecting to GitHub. Check your proxy settings and internet connection."
    exit 1
fi

access_token=$(echo "$response_access_token" | grep -oE 'access_token=[^&]+' | cut -d '=' -f 2)

if [ -z "$access_token" ]; then
    echo "Failed to get access token. GitHub response:"
    echo "$response_access_token"
    exit 1
fi

# Update the token in config.json
jq --arg index "$account_index" --arg token "$access_token" '.providers[] | select(.type == "github-copilot") | .accounts[$index | tonumber].token = $token' config.json > config.tmp && mv config.tmp config.json

if [ $? -eq 0 ]; then
    echo -e "\nToken has been successfully updated for account: $account_id"
else
    echo "Error updating the token in config.json"
    exit 1
fi