# Setting Up Binance Webhook Integration

## Obtaining Webhook Credentials

To receive payment notifications from Binance, you'll need to:

1. Create a Binance Merchant Account:
   - Go to [Binance Merchant Portal](https://merchant.binance.com)
   - Sign up for a merchant account if you haven't already
   - Complete the merchant verification process

2. Get Your Wallet Address:
   - In the Merchant Portal, go to "Wallet Management"
   - Create a new wallet or use an existing one
   - Copy your wallet address

3. Generate Webhook Secret Key:
   - Go to "API Management" in the Merchant Portal
   - Click "Generate New API Key"
   - This will generate:
     - API Key (for API requests)
     - Webhook Secret Key (32-character string, used for signature validation)
   - Example webhook key format: `8a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p`

4. Configure Webhook URL:
   - Still in API Management, add your webhook URL
   - Format: `https://your-api-domain.com/binance-webhook`
   - Binance will send payment notifications to this URL

## Configuration

Update your `config.json` with the obtained credentials:

```json
{
  "binance": {
    "wallet_address": "your_binance_wallet_address",
    "webhook_key": "your_32_char_webhook_secret_key"
  }
}
```

## Security Notes

1. Keep your webhook secret key secure:
   - Never share it publicly
   - Don't commit it to version control
   - Use environment variables in production

2. The webhook key is used to verify that notifications are actually from Binance:
   - Binance signs each webhook payload with this key
   - Our system validates the signature using HMAC-SHA256
   - Invalid signatures are rejected

3. Webhook URL must be HTTPS in production:
   - Binance only sends webhooks to secure endpoints
   - Local development can use HTTP but production must use HTTPS

## Testing

1. Make a test payment:
   - Send a small amount to your wallet
   - Include an API key in the memo field
   - Watch the logs for webhook processing

2. Webhook payload example:
```json
{
  "event_type": "DEPOSIT",
  "txId": "0x123...",
  "amount": "1.0",
  "asset": "USDT",
  "status": "COMPLETED",
  "memo": "sk-your-api-key",
  "timestamp": 1647123456789
}
```

## Troubleshooting

1. Webhook not received:
   - Verify webhook URL is accessible
   - Check Binance Merchant Portal webhook logs
   - Ensure URL is HTTPS in production

2. Invalid signature errors:
   - Double-check webhook key in config
   - Verify webhook key matches Merchant Portal
   - Check for whitespace in key

3. Credits not added:
   - Verify API key in memo field
   - Check supported currencies
   - Review server logs for errors