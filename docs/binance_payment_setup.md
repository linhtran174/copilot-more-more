# Setting Up Binance Payment Integration

## Overview

This system monitors your Binance wallet address for incoming payments and automatically adds credits to API keys. Unlike traditional webhook-based systems, this implementation directly monitors the blockchain for transactions, making it more accessible and requiring no special merchant account.

## Configuration

1. Get a BSCScan API key:
   - Go to [BSCScan](https://bscscan.com)
   - Create an account and verify your email
   - Go to [API Keys](https://bscscan.com/apis) page
   - Click "Create New API Key"
   - Copy your API key

2. Add your configuration to `config.json`:

```json
{
  "binance": {
    "wallet_address": "your_binance_wallet_address",  // Your BSC wallet address
    "bscscan_api_key": "your_bscscan_api_key"        // API key from BSCScan
  }
}
```

Note: Free BSCScan API keys have a rate limit of 5 calls/second. The system is designed to work within these limits.

## How It Works

1. **Transaction Monitoring**:
   - The system periodically checks for new transactions to your wallet address
   - Supports both BNB and BEP-20 token transfers (USDT, BUSD)
   - Automatically processes completed transactions

2. **Credit Conversion Rates**:
   - USDT: 1 USDT = 1 credit
   - BUSD: 1 BUSD = 1 credit
   - BNB: 1 BNB = 300 credits (rate may vary based on market price)

3. **Payment Processing**:
   - System checks for new transactions every 60 seconds
   - When a payment is detected, it looks for an API key in the transaction memo
   - If a valid API key is found, credits are automatically added

## Making Payments

1. Get your API key if you don't have one:
   ```bash
   curl -X POST "http://your-api-host/api-keys"
   ```

2. Send payment to the configured wallet address:
   - Use the BSC (BNB Smart Chain) network
   - Amount: Any amount in supported currencies
   - **Important**: Include your API key in the transaction memo field
   
3. Credits will be added automatically once the transaction is confirmed

## Supported Currencies

- USDT (BEP-20)
- BUSD (BEP-20)
- BNB

## Security

1. Transaction Validation:
   - All transactions are verified on the blockchain
   - Only completed transactions are processed
   - System verifies the recipient address matches configured wallet

2. Credit Processing:
   - Only processes transactions with valid API keys in memo
   - Maintains a record of processed transactions to prevent duplicates
   - Logs all credit additions for audit purposes

## Monitoring & Troubleshooting

1. System Logs:
   - Check server logs for payment processing status
   - Each transaction processing attempt is logged
   - Credit additions are logged with transaction IDs

2. Common Issues:
   - Missing API key in memo: Credits cannot be added
   - Invalid API key: Transaction will be logged but credits not added
   - Unsupported currency: Transaction will be ignored

3. Verifying Credits:
   ```bash
   curl -H "Authorization: Bearer YOUR_API_KEY" "http://your-api-host/balance"
   ```

## Development Notes

1. The system uses BSCScan's API to monitor transactions. In a production environment, you may want to:
   - Add more blockchain networks (Ethereum, etc.)
   - Implement a node connection for direct blockchain access
   - Add rate limiting for blockchain API calls
   - Implement redundancy for transaction monitoring

2. Transaction Processing:
   - Current implementation checks every 60 seconds
   - Maintains a set of processed transaction IDs
   - Handles both native coin (BNB) and token transfers
   - Uses efficient async processing for scalability