import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db
from schemas import Trade

app = FastAPI(title="Trading Journal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Helpers
# -----------------------------

def to_object_id(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id"))
    # Convert datetimes to isoformat
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


def trade_realized_pnl(trade: Dict[str, Any]) -> Optional[float]:
    """Compute realized PnL for closed trades. Returns None if not computable."""
    try:
        if not trade.get("closed"):
            return None
        entry = float(trade.get("entry_price"))
        exitp = float(trade.get("exit_price"))
        qty = float(trade.get("quantity"))
        fees = float(trade.get("fees", 0.0))
        side = trade.get("side")
        pnl = (exitp - entry) * qty if side == "long" else (entry - exitp) * qty
        pnl -= fees
        return pnl
    except Exception:
        return None


# -----------------------------
# Health / Test
# -----------------------------

@app.get("/")
def read_root():
    return {"message": "Trading Journal Backend Running"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# -----------------------------
# Trades CRUD
# -----------------------------

class TradesQuery(BaseModel):
    symbol: Optional[str] = None
    strategy: Optional[str] = None
    tag: Optional[str] = None
    closed: Optional[bool] = None


@app.get("/api/trades")
def list_trades(
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    tag: Optional[str] = None,
    closed: Optional[bool] = None,
    limit: int = Query(200, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    sort: str = Query("-entry_date", description="Field to sort by, prefix with - for desc"),
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    q: Dict[str, Any] = {}
    if symbol:
        q["symbol"] = symbol.upper()
    if strategy:
        q["strategy"] = strategy
    if tag:
        q["tags"] = tag
    if closed is not None:
        q["closed"] = closed

    col = db.trade

    sort_dir = -1 if sort.startswith("-") else 1
    sort_field = sort[1:] if sort.startswith("-") else sort

    cursor = col.find(q).sort(sort_field, sort_dir).skip(skip).limit(limit)
    items = [serialize(d) for d in cursor]

    total = col.count_documents(q)
    return {"items": items, "total": total}


@app.post("/api/trades")
def create_trade(payload: Trade):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    data = payload.model_dump()
    # Normalizations
    data["symbol"] = data["symbol"].upper()
    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)

    # If exit fields present, determine closed
    if data.get("exit_price") and data.get("exit_date"):
        data["closed"] = True

    result = db.trade.insert_one(data)
    return {"id": str(result.inserted_id)}


@app.get("/api/trades/{trade_id}")
def get_trade(trade_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    doc = db.trade.find_one({"_id": to_object_id(trade_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Trade not found")
    return serialize(doc)


@app.put("/api/trades/{trade_id}")
def update_trade(trade_id: str, payload: Dict[str, Any]):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    if "symbol" in payload and isinstance(payload["symbol"], str):
        payload["symbol"] = payload["symbol"].upper()

    payload["updated_at"] = datetime.now(timezone.utc)

    res = db.trade.update_one({"_id": to_object_id(trade_id)}, {"$set": payload})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Trade not found")
    doc = db.trade.find_one({"_id": to_object_id(trade_id)})
    return serialize(doc)


@app.delete("/api/trades/{trade_id}")
def delete_trade(trade_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = db.trade.delete_one({"_id": to_object_id(trade_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"ok": True}


# -----------------------------
# Analytics
# -----------------------------

@app.get("/api/analytics/summary")
def analytics_summary(
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    q: Dict[str, Any] = {}
    if symbol:
        q["symbol"] = symbol.upper()
    if strategy:
        q["strategy"] = strategy
    # Date filter on entry_date
    if start or end:
        date_filter: Dict[str, Any] = {}
        if start:
            date_filter["$gte"] = datetime.fromisoformat(start)
        if end:
            date_filter["$lte"] = datetime.fromisoformat(end)
        q["entry_date"] = date_filter

    trades = list(db.trade.find(q))

    total = len(trades)
    closed_trades = [t for t in trades if t.get("closed")]

    pnls: List[float] = []
    wins: List[float] = []
    losses: List[float] = []

    for t in closed_trades:
        pnl = trade_realized_pnl(t)
        if pnl is None:
            continue
        pnls.append(pnl)
        if pnl >= 0:
            wins.append(pnl)
        else:
            losses.append(pnl)

    net_pnl = sum(pnls) if pnls else 0.0
    win_rate = (len(wins) / len(pnls) * 100.0) if pnls else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if wins and losses else (float('inf') if wins and not losses else 0.0)
    expectancy = ((win_rate/100.0) * avg_win) + ((1 - win_rate/100.0) * avg_loss)

    # Group by symbol
    by_symbol: Dict[str, float] = {}
    for t in closed_trades:
        pnl = trade_realized_pnl(t)
        if pnl is None:
            continue
        sym = t.get("symbol")
        by_symbol[sym] = by_symbol.get(sym, 0.0) + pnl

    # Group by strategy
    by_strategy: Dict[str, float] = {}
    for t in closed_trades:
        pnl = trade_realized_pnl(t)
        if pnl is None:
            continue
        strat = t.get("strategy") or "Unlabeled"
        by_strategy[strat] = by_strategy.get(strat, 0.0) + pnl

    # Monthly PnL (YYYY-MM)
    monthly: Dict[str, float] = {}
    for t in closed_trades:
        pnl = trade_realized_pnl(t)
        if pnl is None:
            continue
        d = t.get("exit_date") or t.get("entry_date")
        if isinstance(d, str):
            d = datetime.fromisoformat(d)
        if isinstance(d, datetime):
            key = d.strftime("%Y-%m")
            monthly[key] = monthly.get(key, 0.0) + pnl

    return {
        "total_trades": total,
        "closed_trades": len(closed_trades),
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "by_symbol": by_symbol,
        "by_strategy": by_strategy,
        "monthly": monthly,
    }


@app.get("/api/analytics/calendar")
def analytics_calendar(
    start: Optional[str] = None,
    end: Optional[str] = None,
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    # Determine date window
    if end:
        end_dt = datetime.fromisoformat(end)
    else:
        end_dt = datetime.now(timezone.utc)
    if start:
        start_dt = datetime.fromisoformat(start)
    else:
        start_dt = end_dt - timedelta(days=180)

    q: Dict[str, Any] = {
        "closed": True,
        "exit_date": {"$gte": start_dt, "$lte": end_dt},
    }

    trades = list(db.trade.find(q))

    by_day: Dict[str, float] = {}
    for t in trades:
        pnl = trade_realized_pnl(t)
        if pnl is None:
            continue
        d = t.get("exit_date")
        if isinstance(d, str):
            d = datetime.fromisoformat(d)
        day_key = d.date().isoformat()
        by_day[day_key] = by_day.get(day_key, 0.0) + pnl

    return {"start": start_dt.date().isoformat(), "end": end_dt.date().isoformat(), "daily": by_day}


@app.get("/api/analytics/equity_curve")
def analytics_equity_curve():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    trades = list(db.trade.find({"closed": True, "exit_price": {"$ne": None}}).sort("exit_date", 1))

    curve: List[Dict[str, Any]] = []
    cum = 0.0
    for t in trades:
        pnl = trade_realized_pnl(t)
        if pnl is None:
            continue
        cum += pnl
        d = t.get("exit_date")
        if isinstance(d, str):
            d = datetime.fromisoformat(d)
        curve.append({"date": d.date().isoformat(), "equity": cum})

    return {"points": curve}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
