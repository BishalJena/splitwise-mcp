# splitwise_mcp.py
"""
A Model Context Protocol (MCP) service for Splitwise using FastAPI.

Features:
1. Loads Splitwise OpenAPI spec to validate/map requests.
2. Exposes MCP endpoints for common Splitwise actions:
   - create_expense, get_balance
   - list_groups, get_group_details
   - list_expenses, get_expense_details
   - list_friends, add_friend
   - create_group, delete_group
3. Normalizes responses for AI models/agents.

Deployment:
- Dockerfile included.
- Deploy on Vercel, Render, Railway, Fly.io, etc.

Cursor IDE:
- Open as project, install requirements, run `uvicorn splitwise_mcp:app --reload`.
"""
from dotenv import load_dotenv
load_dotenv()

import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Any
import os

# Load Splitwise OpenAPI spec (for future schema validation)
with open('openapi.json') as f:
    splitwise_spec = json.load(f)

# Configuration via environment or hardcode for demo
SPLITWISE_BASE = "https://secure.splitwise.com/api/v3.0"
API_KEY = os.getenv('API_KEY', 'YOUR_SPLITWISE_API_KEY')

# ------------------ Pydantic Models ------------------
class ExpenseIntent(BaseModel):
    user_id: int
    amount: float
    currency: str = "INR"
    description: str
    participants: List[int]
    split_type: str = "equal"  # or 'shares' or 'unequal'
    # For 'shares' or 'unequal', specify how much each participant owes
    owed_shares: Optional[List[float]] = None  # Must match participants order

class FriendIntent(BaseModel):
    user_email: str
    first_name: Optional[str]
    last_name: Optional[str]

class GroupIntent(BaseModel):
    name: str
    group_type: Optional[str] = 'other'
    simplify_by_default: Optional[bool] = False
    users: Optional[List[int]] = []

class MCPResponse(BaseModel):
    status: str
    data: Any

app = FastAPI(title="Splitwise MCP Service")

# ✅ Add root route for Render health check
@app.get("/", include_in_schema=False)
async def root():
    return {"status": "Splitwise MCP is running"}

# ------------------ Helper ------------------
async def call_splitwise(method: str, path: str, payload: dict = None, params: dict = None):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    url = f"{SPLITWISE_BASE}{path}"
    async with httpx.AsyncClient() as client:
        response = await client.request(method, url, json=payload, params=params, headers=headers)
        if response.status_code not in (200, 201):
            raise HTTPException(status_code=response.status_code, detail=response.text)
        return response.json()

# ------------------ Expense Endpoints ------------------
# Example usage for /mcp/create_expense:
#
# Equal split (2 users, 400 total):
# {
#   "user_id": 1,
#   "amount": 400,
#   "currency": "INR",
#   "description": "Dinner",
#   "participants": [1, 2],
#   "split_type": "equal"
# }
#
# Unequal split (2 users, 400 total, split as 135 and 265):
# {
#   "user_id": 1,
#   "amount": 400,
#   "currency": "INR",
#   "description": "Dinner",
#   "participants": [1, 2],
#   "split_type": "unequal",
#   "owed_shares": [135, 265]
# }
#
# Shares split (3 users, 100 total, split as 50, 30, 20):
# {
#   "user_id": 1,
#   "amount": 100,
#   "currency": "INR",
#   "description": "Snacks",
#   "participants": [1, 2, 3],
#   "split_type": "shares",
#   "owed_shares": [50, 30, 20]
# }
@app.post('/mcp/create_expense', response_model=MCPResponse)
async def mcp_create_expense(intent: ExpenseIntent):
    body = {"cost": f"{intent.amount:.2f}",
            "description": intent.description,
            "currency_code": intent.currency}
    
    if intent.split_type == "equal":
        share = round(intent.amount / len(intent.participants), 2)
        for idx, uid in enumerate(intent.participants):
            paid = f"{intent.amount:.2f}" if uid == intent.user_id else "0.00"
            owed = f"{share:.2f}" if uid != intent.user_id else "0.00"
            body[f"users__{idx}__user_id"] = uid
            body[f"users__{idx}__paid_share"] = paid
            body[f"users__{idx}__owed_share"] = owed
    elif intent.split_type in ("shares", "unequal"):
        if not intent.owed_shares or len(intent.owed_shares) != len(intent.participants):
            raise HTTPException(status_code=400, detail="owed_shares must be provided and match participants length for 'shares' or 'unequal' split_type.")
        for idx, (uid, owed_share) in enumerate(zip(intent.participants, intent.owed_shares)):
            paid = f"{intent.amount:.2f}" if uid == intent.user_id else "0.00"
            owed = f"{owed_share:.2f}"
            body[f"users__{idx}__user_id"] = uid
            body[f"users__{idx}__paid_share"] = paid
            body[f"users__{idx}__owed_share"] = owed
    else:
        raise HTTPException(status_code=400, detail="Invalid split_type. Use 'equal', 'shares', or 'unequal'.")
    
    api_resp = await call_splitwise('POST', '/create_expense', payload=body)
    return {"status": "success", "data": api_resp}

@app.get('/mcp/list_expenses', response_model=MCPResponse)
async def mcp_list_expenses(user_id: Optional[int] = None, group_id: Optional[int] = None):
    params = {}
    if group_id:
        params['group_id'] = group_id
    elif user_id:
        params['friend_id'] = user_id
    api_resp = await call_splitwise('GET', '/get_expenses', params=params)
    return {"status": "success", "data": api_resp}

@app.get('/mcp/get_expense/{expense_id}', response_model=MCPResponse)
async def mcp_get_expense(expense_id: int):
    api_resp = await call_splitwise('GET', f'/get_expense/{expense_id}')
    return {"status": "success", "data": api_resp}

@app.get('/mcp/get_balance/{user_id}', response_model=MCPResponse)
async def mcp_get_balance(user_id: int):
    api_resp = await call_splitwise('GET', '/get_current_user')
    return {"status": "success", "data": api_resp}

# ------------------ Group Endpoints ------------------
@app.post('/mcp/create_group', response_model=MCPResponse)
async def mcp_create_group(intent: GroupIntent):
    body = {"name": intent.name,
            "group_type": intent.group_type,
            "simplify_by_default": intent.simplify_by_default}
    for idx, uid in enumerate(intent.users):
        body[f"users__{idx}__user_id"] = uid
    api_resp = await call_splitwise('POST', '/create_group', payload=body)
    return {"status": "success", "data": api_resp}

@app.post('/mcp/delete_group/{group_id}', response_model=MCPResponse)
async def mcp_delete_group(group_id: int):
    api_resp = await call_splitwise('POST', f'/delete_group/{group_id}')
    return {"status": "success", "data": api_resp}

@app.get('/mcp/list_groups', response_model=MCPResponse)
async def mcp_list_groups():
    api_resp = await call_splitwise('GET', '/get_groups')
    return {"status": "success", "data": api_resp}

@app.get('/mcp/get_group/{group_id}', response_model=MCPResponse)
async def mcp_get_group(group_id: int):
    api_resp = await call_splitwise('GET', f'/get_group/{group_id}')
    return {"status": "success", "data": api_resp}

# ------------------ Friend Endpoints ------------------
@app.get('/mcp/list_friends', response_model=MCPResponse)
async def mcp_list_friends():
    api_resp = await call_splitwise('GET', '/get_friends')
    return {"status": "success", "data": api_resp}

@app.post('/mcp/add_friend', response_model=MCPResponse)
async def mcp_add_friend(intent: FriendIntent):
    body = {"user_email": intent.user_email}
    if intent.first_name:
        body['user_first_name'] = intent.first_name
    if intent.last_name:
        body['user_last_name'] = intent.last_name
    api_resp = await call_splitwise('POST', '/create_friend', payload=body)
    return {"status": "success", "data": api_resp}

@app.post('/mcp/delete_friend/{friend_id}', response_model=MCPResponse)
async def mcp_delete_friend(friend_id: int):
    api_resp = await call_splitwise('POST', f'/delete_friend/{friend_id}')
    return {"status": "success", "data": api_resp}

# ------------------ Run ------------------
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
