#!/bin/bash

# Client ID for the vscode copilot
client_id="01ab8ac9400c4e429b23" 

echo "How many tokens do you want to generate? (Enter a number)"
read token_count

# Initialize config object with defaults if config.json doesn't exist
if [ -f "config.json" ]; then
    config=$(cat config.json)
else
    config='{
        "refresh_tokens": [],
        "request_timeout": 100,
        "record_traffic": true
    }'
fi

for ((i=1; i<=token_count; i++)); do
    echo "Generating token $i of $token_count..."
    
    # Get the response from the first curl command (silently)
    response=$(curl -s https://github.com/login/device/code -X POST -d "client_id=$client_id&scope=user:email")

    # Extract codes
    device_code=$(echo "$response" | grep -oE 'device_code=[^&]+' | cut -d '=' -f 2)
    user_code=$(echo "$response" | grep -oE 'user_code=[^&]+' | cut -d '=' -f 2)

    # Print instructions for the user
    echo "Please open https://github.com/login/device/ and enter the following code: $user_code"
    echo "Press Enter once you have authorized the application..."
    read

    # Get the access token (silently)
    response_access_token=$(curl -s https://github.com/login/oauth/access_token -X POST -d "client_id=$client_id&scope=user:email&device_code=$device_code&grant_type=urn:ietf:params:oauth:grant-type:device_code")

    access_token=$(echo "$response_access_token" | grep -oE 'access_token=[^&]+' | cut -d '=' -f 2)
    
    # Add new token to config using jq
    config=$(echo "$config" | jq --arg token "$access_token" '.refresh_tokens += [{"token": $token}]')
    
    echo "Token $i generated successfully!"
    
    if [ $i -lt $token_count ]; then
        echo -e "\nPreparing to generate next token..."
    fi
done

# Save updated config to config.json
echo "$config" | jq '.' > config.json

echo -e "\nAll tokens have been generated and saved to config.json file!"
