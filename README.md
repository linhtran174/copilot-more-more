# copilot-more-more

`copilot-more-more` is a fork of `copilot-more` that allows routing between multiple accounts, which each account owning a separate proxy to prevent IP-ban. This is useful if you want to use more than the rate-limit for certain model

## Ethical Use
- Respect the GitHub Copilot terms of service.
- Minimize the use of the models for non-coding purposes.
- Be mindful of the risk of being banned by GitHub Copilot for misuse.


## 🏃‍♂️ How to Run
TLDR: `bash run_me.sh`

1. `init.sh` will help you add new accounts into the system
2. `run_me.sh` will auto check dependancies and run the server

## ✨ Magic Time
Now you can connect Cline or any other AI client to `http://localhost:15432` and start coding with the power of GPT-4o and Claude-3.5-Sonnet without worrying about the cost. Note, the copilot-more manages the access token, you can use whatever string as API keys if Cline or the AI tools ask for one.

### 🚀 Cline Integration

1. Install Cline `code --install-extension saoudrizwan.claude-dev`
2. Open Cline and go to the settings
3. Set the following:
     * **API Provider**: `OpenAI Compatible`
     * **API URL**: `http://localhost:15432`
     * **API Key**: `anyting`
     * **Model**: `gpt-4o`, `claude-3.5-sonnet`, `o1`, `o1-mini`


## 🔍 Debugging

For troubleshooting integration issues, you can enable traffic logging to inspect the API requests and responses.

### Traffic Logging

To enable logging, set the `RECORD_TRAFFIC` environment variable:

```bash
RECORD_TRAFFIC=true REFRESH_TOKEN=gho_xxxx poetry run uvicorn copilot_more.server:app --port 15432
```

All traffic will be logged to files in the current directory with the naming pattern: copilot_traffic_YYYYMMDD_HHMMSS.mitm

Attach this file when reporting issues.

Note: the Authorization header has ben redacted. So the refresh token won't be leaked.

## 🤔 Limitation

The GH Copilot models sit behind an API server that is not fully compatible with the OpenAI API. You cannot pass in a message like this:

```json
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "<task>\nreview the code\n</task>"
        },
        {
          "type": "text",
          "text": "<task>\nreview the code carefully\n</task>"
        }
      ]
    }
```
copilot-more takes care of this limitation by converting the message to a format that the GH Copilot API understands. However, without the `type`, we cannot leverage the models' vision capabilities, so that you cannot do screenshot analysis.
