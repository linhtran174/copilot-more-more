import json
import time
import asyncio
import aiohttp
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from copilot_more.logger import logger

@dataclass
class BinancePayment:
    """Represents a Binance payment."""
    payment_id: str
    amount: float
    currency: str
    wallet_address: str
    status: str
    memo: Optional[str] = None
    created_at: Optional[datetime] = None

class BinancePaymentManager:
    """Manages Binance payment processing through direct blockchain monitoring."""
    
    def __init__(self, wallet_address: str, bscscan_api_key: str):
        self.wallet_address = wallet_address
        self.bscscan_api_key = bscscan_api_key
        self.last_check_time = int(time.time() * 1000)  # Binance uses milliseconds
        self.processed_txns = set()  # Keep track of processed transactions
        self.running = False
        self.check_interval = 60  # Check every 60 seconds
        self.base_url = "https://api.bscscan.com/api"

    async def start_monitoring(self):
        """Start monitoring wallet transactions."""
        self.running = True
        while self.running:
            try:
                payments = await self.check_transactions()
                for payment in payments:
                    logger.info(f"New payment detected: {payment.payment_id}")
                    # Process payment here
                    if payment.memo and payment.memo.startswith('sk-'):
                        credits = self.calculate_credits(payment)
                        if credits > 0:
                            from copilot_more.api_key_manager import api_key_manager
                            if api_key_manager.add_credits(payment.memo, credits):
                                logger.info(f"Added {credits} credits to {payment.memo}")
                            else:
                                logger.error(f"Failed to add credits for payment {payment.payment_id}")
            except Exception as e:
                logger.error(f"Error in monitoring loop: {str(e)}")
            await asyncio.sleep(self.check_interval)

    def stop_monitoring(self):
        """Stop monitoring wallet transactions."""
        self.running = False

    async def check_transactions(self) -> List[BinancePayment]:
        """Check for new transactions to the wallet."""
        current_time = int(time.time() * 1000)
        payments = []
        
        try:
            # BSC API endpoint (you can add more chains as needed)
            async with aiohttp.ClientSession() as session:
                # BSC Mainnet transactions - BEP-20 tokens
                params = {
                    "module": "account",
                    "action": "tokentx",  # For BEP-20 tokens
                    "address": self.wallet_address,
                    "starttime": str(self.last_check_time // 1000),
                    "endtime": str(current_time // 1000),
                    "sort": "asc",
                    "apikey": self.bscscan_api_key
                }
                
                async with session.get(self.base_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data["status"] == "1" and data["result"]:
                            for tx in data["result"]:
                                if tx["hash"] not in self.processed_txns and \
                                   tx["to"].lower() == self.wallet_address.lower():
                                    payment = self._process_transaction(tx)
                                    if payment:
                                        payments.append(payment)
                                        self.processed_txns.add(tx["hash"])
                    
                # Also check for BNB transfers
                bnb_params = {
                    "module": "account",
                    "action": "txlist",  # For BNB transfers
                    "address": self.wallet_address,
                    "starttime": str(self.last_check_time // 1000),
                    "endtime": str(current_time // 1000),
                    "sort": "asc",
                    "apikey": self.bscscan_api_key
                }
                async with session.get(self.base_url, params=bnb_params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data["status"] == "1" and data["result"]:
                            for tx in data["result"]:
                                if tx["hash"] not in self.processed_txns and \
                                   tx["to"].lower() == self.wallet_address.lower():
                                    payment = self._process_transaction(tx)
                                    if payment:
                                        payments.append(payment)
                                        self.processed_txns.add(tx["hash"])
        
        except Exception as e:
            logger.error(f"Error fetching transactions: {str(e)}")
        
        self.last_check_time = current_time
        return payments

    def _process_transaction(self, tx: Dict[str, Any]) -> Optional[BinancePayment]:
        """Process a transaction and return payment info if valid."""
        try:
            # Extract memo from input data if available
            memo = None
            if tx.get("input") and len(tx["input"]) > 10:
                try:
                    # Attempt to decode hex data as UTF-8 string
                    memo_hex = tx["input"][10:]  # Skip function selector
                    memo = bytes.fromhex(memo_hex).decode('utf-8').strip('\x00')
                except:
                    pass

            return BinancePayment(
                payment_id=tx["hash"],
                amount=float(tx["value"]) / (10 ** 18),  # Convert from wei/decimals
                currency=tx.get("tokenSymbol", "BNB"),
                wallet_address=tx["to"],
                status="COMPLETED",
                memo=memo,
                created_at=datetime.fromtimestamp(int(tx["timeStamp"]))
            )
        except (KeyError, ValueError) as e:
            logger.error(f"Error processing transaction {tx.get('hash')}: {str(e)}")
            return None

    def calculate_credits(self, payment: BinancePayment) -> float:
        """Calculate credits based on payment amount and currency."""
        rates = {
            'USDT': 1.0,    # 1 USDT = 1 credit
            'BNB': 300.0,   # 1 BNB = 300 credits (adjust based on current price)
            'BUSD': 1.0,    # 1 BUSD = 1 credit
        }
        
        rate = rates.get(payment.currency)
        if not rate:
            logger.error(f"Unsupported currency: {payment.currency}")
            return 0.0
            
        return payment.amount * rate