"""
Database Schemas for Trading Journal

Each Pydantic model here represents a MongoDB collection. The collection name
is the lowercase of the class name (e.g., Trade -> "trade").
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

# ==========================
# Core Collections
# ==========================

class Trade(BaseModel):
    """
    Trading journal entry
    Collection: trade
    """
    symbol: str = Field(..., description="Ticker or instrument symbol, e.g., AAPL, BTCUSDT")
    side: Literal["long", "short"] = Field(..., description="Direction of the trade")
    strategy: Optional[str] = Field(None, description="Strategy name/label")

    entry_date: datetime = Field(..., description="Entry datetime (UTC)")
    exit_date: Optional[datetime] = Field(None, description="Exit datetime (UTC)")

    entry_price: float = Field(..., gt=0, description="Entry price")
    exit_price: Optional[float] = Field(None, gt=0, description="Exit price (if closed)")

    quantity: float = Field(..., gt=0, description="Position size in units (shares/contracts)")
    fees: float = Field(0.0, ge=0, description="Total fees/commissions for the trade")

    setup: Optional[str] = Field(None, description="Setup description or tags")
    notes: Optional[str] = Field(None, description="Free-form notes")
    tags: List[str] = Field(default_factory=list, description="List of tags for filtering")

    risk_amount: Optional[float] = Field(None, ge=0, description="Planned risk in currency")
    stop_loss: Optional[float] = Field(None, ge=0, description="Stop loss price")
    take_profit: Optional[float] = Field(None, ge=0, description="Take profit price")

    closed: bool = Field(False, description="Whether the trade is closed")

class Strategy(BaseModel):
    """User-defined strategies for grouping and analysis"""
    name: str
    description: Optional[str] = None
    tags: List[str] = []

class Tag(BaseModel):
    """Free-form tag catalog (optional)"""
    name: str
    color: Optional[str] = Field(None, description="Hex color like #10b981")

# You can extend with Account, Session, Screenshot, etc., as needed.
