import time
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Dict
from copilot_more.api_key_manager import api_key_manager
from copilot_more.logger import logger

router = APIRouter()

class ApiKeyResponse(BaseModel):
    """Response model for API key operations."""
    key: str
    credits: float
    total_tokens_used: int
    enabled: bool

class BalanceResponse(BaseModel):
    """Response model for balance check."""
    credits: float
    total_tokens_used: int

class AddCreditsRequest(BaseModel):
    """Request model for adding credits."""
    amount: float

def get_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    """Validate API key from header."""
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required"
        )
    
    key_info = api_key_manager.get_key_info(x_api_key)
    if not key_info:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )
    
    if not key_info.enabled:
        raise HTTPException(
            status_code=403,
            detail="API key is disabled"
        )
    
    return x_api_key

@router.post("/api-keys", response_model=ApiKeyResponse)
async def create_api_key(initial_credits: float = 0.0):
    """Create a new API key."""
    try:
        # Use user ID format key-{timestamp} for now
        # In a real system, this would come from authentication
        user_id = f"user-{int(time.time())}"
        api_key = api_key_manager.create_api_key(user_id, initial_credits)
        key_info = api_key_manager.get_key_info(api_key)
        if not key_info:
            raise HTTPException(status_code=500, detail="Error retrieving API key info")
            
        return ApiKeyResponse(
            key=api_key,
            credits=key_info.credits,
            total_tokens_used=key_info.total_tokens_used,
            enabled=key_info.enabled
        )
    except Exception as e:
        logger.error(f"Error creating API key: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating API key")

@router.get("/balance", response_model=BalanceResponse)
async def get_balance(api_key: str = Header(..., alias="X-API-Key")):
    """Get current balance and usage for an API key."""
    key_info = api_key_manager.get_key_info(api_key)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return BalanceResponse(
        credits=key_info.credits,
        total_tokens_used=key_info.total_tokens_used
    )

@router.post("/add-credits")
async def add_credits(request: AddCreditsRequest, api_key: str = Header(..., alias="X-API-Key")):
    """Add credits to an API key."""
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    success = api_key_manager.add_credits(api_key, request.amount)
    if not success:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    key_info = api_key_manager.get_key_info(api_key)
    if not key_info:
        raise HTTPException(status_code=500, detail="Error retrieving API key info")

    return {
        "credits": key_info.credits,
        "message": f"Successfully added {request.amount} credits"
    }

@router.post("/disable")
async def disable_api_key(api_key: str = Header(..., alias="X-API-Key")):
    """Disable an API key."""
    success = api_key_manager.disable_key(api_key)
    if not success:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {"message": "API key disabled"}

@router.post("/enable")
async def enable_api_key(api_key: str = Header(..., alias="X-API-Key")):
    """Enable an API key."""
    success = api_key_manager.enable_key(api_key)
    if not success:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {"message": "API key enabled"}