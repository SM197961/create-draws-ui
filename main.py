# main.py
import os
import json
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict
import time
from fastapi import Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

# -----------------------------
# Environment / Config
# -----------------------------
load_dotenv()  # Load .env if present
MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN")
MONDAY_API_URL = "https://api.monday.com/v2"

DRAWS_BOARD_ID = os.getenv("DRAWS_BOARD_ID")
DRAWS_GROUP_ID = os.getenv("DRAWS_GROUP_ID", "group_mkvhnpjn")
DRAWS_CONNECT_COL_ID = os.getenv("DRAWS_CONNECT_COL_ID", "board_relation_mkvjmtnw")
DRAWS_REQUESTED_COL_ID = os.getenv("DRAWS_REQUESTED_COL_ID", "numeric_mkvjfd2x")  # numbers
DRAWS_DATE_SUBMITTED_COL_ID = os.getenv("DRAWS_DATE_SUBMITTED_COL_ID", "date_mkvhrddp")  # date
DRAWS_STATUS_COL_ID = os.getenv("DRAWS_STATUS_COL_ID", "status")  # status
DRAWS_STATUS_LABEL = os.getenv("DRAWS_STATUS_LABEL", "Working on it")

DRAWS_DRAWNUM_COL_ID = os.getenv("DRAWS_DRAWNUM_COL_ID", "numeric_mkvh3qbd")  # Draw # numbers column

SERVICING_BOARD_ID = os.getenv("SERVICING_BOARD_ID")
SERVICING_DRAW_COL_IDS = os.getenv(
    "SERVICING_DRAW_COL_IDS",
    "numeric_mkvhw603,numeric_mkvhw2az,numeric_mkvht40c,numeric_mkvh14tv,numeric_mkvhvcj8",
)
SERVICING_LAST_DRAW_NUM_COL_ID = os.getenv(
    "SERVICING_LAST_DRAW_NUM_COL_ID",
    "numeric_mkvjry87",
)
SERVICING_LOAN_NUMBER_COL_ID = os.getenv("SERVICING_LOAN_NUMBER_COL_ID", "text_mkvgmaer")

API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")  # If set, endpoints require 'Authorization: Bearer <token>'
ALLOW_ORIGINS = os.getenv("ALLOW_ORIGINS", "*")  # Comma-separated list or '*'
IDEMPOTENCY_SCAN_LIMIT = int(os.getenv("IDEMPOTENCY_SCAN_LIMIT", "200"))

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(
    title="Draw Creator Service",
    version="1.0.0"
)

_cors_origins = [o.strip() for o in ALLOW_ORIGINS.split(",")] if ALLOW_ORIGINS else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def require_auth(authorization: Optional[str] = Header(None)):
    if API_AUTH_TOKEN:
        expected = f"Bearer {API_AUTH_TOKEN}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")
    return True


# -----------------------------
# Models
# -----------------------------
class DrawRequest(BaseModel):
    loan_item_id: Optional[int] = Field(None, description="Servicing item ID on Monday (optional if loan_number provided)")
    loan_number: str = Field(..., description="Human loan number like L00001")
    requested_amount: Optional[float] = Field(
        None, description="Requested draw amount. If omitted, column is not set."
    )

    @field_validator("loan_number")
    def non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("loan_number cannot be empty")
        return v


class DrawResponse(BaseModel):
    ok: bool
    draw_item_id: Optional[str] = None
    loan_item_id: Optional[int] = None
    loan_number: Optional[str] = None
    requested_amount: Optional[float] = None
    draw_number: Optional[int] = None
    error: Optional[str] = None


# -----------------------------
# Monday API helper
# -----------------------------
def monday_api(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {
        "Authorization": MONDAY_API_TOKEN,
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}
    backoff = 0.5
    for attempt in range(4):
        try:
            resp = requests.post(MONDAY_API_URL, headers=headers, json=payload, timeout=15)
            status = resp.status_code
            if status == 200:
                data = resp.json()
                if "errors" in data and data["errors"]:
                    err_msgs = "; ".join(e.get("message", str(e)) for e in data["errors"])
                    raise RuntimeError(f"GraphQL error: {err_msgs}")
                return data.get("data", {})
            # Retry on 429 or 5xx
            if status in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            raise RuntimeError(f"HTTP {status}: {resp.text}")
        except requests.RequestException as re:
            if attempt == 3:
                raise RuntimeError(f"Network error contacting Monday: {re}")
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError("Monday API call failed after retries")


# -----------------------------
# Utility parsing
# -----------------------------
def _parse_numberish(text: Optional[str], value: Optional[str]) -> float:
    if text:
        t = text.replace(",", "").strip()
        try:
            return float(t)
        except Exception:
            pass
    if value:
        try:
            v = json.loads(value)
            if isinstance(v, dict):
                amt = v.get("amount")
                if amt is None and "value" in v:
                    amt = v.get("value")
                if amt is not None:
                    try:
                        return float(str(amt).replace(",", ""))
                    except Exception:
                        return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                return float(v.replace(",", ""))
        except Exception:
            s = value.replace(",", "").strip()
            try:
                return float(s)
            except Exception:
                return 0.0
    return 0.0


def _format_amount_string(amount: float) -> str:
    if amount is None:
        return ""
    if float(amount).is_integer():
        return str(int(amount))
    return str(float(amount))


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _lookup_servicing_item_id_by_loan_number(loan_number: str) -> Optional[int]:
    """
    Look up a Servicing item id by the loan number text column.
    Tries the configured column id first; falls back to matching the item name.
    Returns an int id or None if not found.
    """
    if not loan_number:
        return None
    query = """
    query FindByLoan($boardId: [ID!], $colId: String!, $val: String!) {
      boards(ids: $boardId) {
        items_page(
          limit: 2,
          query_params: {
            rules: [{ column_id: $colId, operator: any_of, compare_value: $val }]
          }
        ) {
          items { id name }
        }
      }
    }
    """
    variables = {
        "boardId": [str(SERVICING_BOARD_ID)],
        "colId": str(SERVICING_LOAN_NUMBER_COL_ID),
        "val": loan_number,
    }
    try:
        data = monday_api(query, variables)
        boards = data.get("boards") or []
        if boards:
            items_page = (boards[0].get("items_page") or {})
            items = items_page.get("items") or []
            if items:
                return int(items[0]["id"])
    except Exception:
        pass
    # Fallback: search by item name equals loan_number
    query2 = """
    query FindByName($boardId: [ID!]) {
      boards(ids: $boardId) {
        items_page(limit: 200) { items { id name } }
      }
    }
    """
    try:
        data2 = monday_api(query2, {"boardId": [str(SERVICING_BOARD_ID)]})
        boards2 = data2.get("boards") or []
        if boards2:
            items = ((boards2[0].get("items_page") or {}).get("items")) or []
            for it in items:
                if (it.get("name") or "").strip() == loan_number.strip():
                    return int(it["id"])
    except Exception:
        pass
    return None


def _draw_item_exists_for(loan_item_id: int, draw_number: int) -> bool:
    """
    Scan recent Draws items and see if one already links to loan_item_id with the same Draw #.
    Uses a lightweight items_page call and checks the relation + numbers client-side.
    """
    query = f"""
    query DrawsScan($boardId: [ID!], $limit: Int!) {{
      boards(ids: $boardId) {{
        items_page(limit: $limit) {{
          items {{
            id
            column_values(ids: [
              "{DRAWS_CONNECT_COL_ID}",
              "{DRAWS_DRAWNUM_COL_ID}"
            ]) {{
              id
              text
              value
              type
            }}
          }}
        }}
      }}
    }}
    """
    data = monday_api(query, {"boardId": [str(DRAWS_BOARD_ID)], "limit": IDEMPOTENCY_SCAN_LIMIT})
    boards = data.get("boards") or []
    if not boards:
        return False
    items = ((boards[0].get("items_page") or {}).get("items")) or []
    target_draw = str(draw_number)
    for it in items:
        cvs = it.get("column_values") or []
        rel = next((cv for cv in cvs if cv.get("id") == DRAWS_CONNECT_COL_ID), None)
        drw = next((cv for cv in cvs if cv.get("id") == DRAWS_DRAWNUM_COL_ID), None)
        # parse relation value for item_ids
        linked_ids = []
        if rel and rel.get("value"):
            try:
                v = json.loads(rel["value"])
                # handles both {"linkedPulseIds":[{"linkedPulseId":"123"}]} and newer shapes
                linked = v.get("linkedPulseIds") or v.get("linkedPulseIds_v2") or []
                for obj in linked:
                    lid = obj.get("linkedPulseId") or obj.get("linkedPulseId_v2") or obj.get("pulseId")
                    if lid:
                        linked_ids.append(int(str(lid)))
            except Exception:
                pass
        draw_txt = (drw.get("text") if drw else "") or ""
        if loan_item_id in linked_ids and draw_txt.strip() == target_draw:
            return True
    return False


# -----------------------------
# Core logic
# -----------------------------
def get_next_draw_number(loan_item_id: int) -> int:
    col_ids = [c.strip() for c in SERVICING_DRAW_COL_IDS.split(",") if c.strip()]
    if not col_ids:
        raise RuntimeError("SERVICING_DRAW_COL_IDS is not configured")

    query = """
    query ServicingDraws($itemIds: [ID!], $colIds: [String!]) {
      items(ids: $itemIds) {
        id
        column_values(ids: $colIds) {
          id
          text
          value
        }
      }
    }
    """
    variables = {"itemIds": [str(loan_item_id)], "colIds": col_ids}
    data = monday_api(query, variables)

    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"Servicing item {loan_item_id} not found")

    col_vals = items[0].get("column_values") or []
    count_paid = 0
    for cv in col_vals:
        n = _parse_numberish(cv.get("text"), cv.get("value"))
        if n > 0:
            count_paid += 1
    return count_paid + 1


def _get_loan_number_from_item_name(item_id: int) -> str:
    """
    Fetch the loan number for a given item, preferring the loan number column if present,
    else falling back to the item name, else the raw item id as string.
    """
    q = """
    query($ids:[ID!], $colId:String!) {
      items(ids:$ids) {
        id
        name
        column_values(ids: [$colId]) { text }
      }
    }
    """
    d = monday_api(q, {"ids": [str(item_id)], "colId": str(SERVICING_LOAN_NUMBER_COL_ID)})
    items = d.get("items") or []
    if not items:
        return str(item_id)
    it = items[0]
    cvs = it.get("column_values") or []
    loan_text = ""
    if cvs:
        cv = cvs[0] or {}
        loan_text = cv.get("text") or ""
    _name = it.get("name") or ""
    # Preferred: loan_text (if present), else item name, else raw id as string
    if loan_text and loan_text.strip():
        return loan_text.strip()
    elif _name and _name.strip():
        return _name.strip()
    else:
        return str(item_id)


def create_draw_item(loan_item_id: int, loan_number: str, requested_amount: Optional[float]) -> Dict[str, Any]:
    # Resolve servicing item id if not provided
    if loan_item_id is None:
        resolved_id = _lookup_servicing_item_id_by_loan_number(loan_number)
        if resolved_id is None:
            raise RuntimeError(f"Could not resolve servicing item for loan_number '{loan_number}'")
        loan_item_id = resolved_id
    # Determine next draw number
    N = get_next_draw_number(loan_item_id)

    # Idempotency: if an item already exists for this loan and draw number, do not create again
    if _draw_item_exists_for(loan_item_id, N):
        # Return a synthetic response indicating "already exists"
        return {"draw_item_id": "already_exists", "draw_number": N}

    item_name = f"{loan_number}"

    cols: Dict[str, Any] = {
        DRAWS_CONNECT_COL_ID: {"item_ids": [loan_item_id]},
        DRAWS_DATE_SUBMITTED_COL_ID: {"date": _today_utc_date()},
        DRAWS_STATUS_COL_ID: {"label": DRAWS_STATUS_LABEL},
        DRAWS_DRAWNUM_COL_ID: _format_amount_string(N),
    }
    if requested_amount is not None:
        cols[DRAWS_REQUESTED_COL_ID] = _format_amount_string(requested_amount)

    create_mutation = """
    mutation CreateItem($boardId: ID!, $groupId: String!, $name: String!, $cols: JSON!) {
      create_item(board_id: $boardId, group_id: $groupId, item_name: $name, column_values: $cols) {
        id
      }
    }
    """
    variables = {
        "boardId": str(DRAWS_BOARD_ID),
        "groupId": str(DRAWS_GROUP_ID),
        "name": item_name,
        "cols": json.dumps(cols),
    }
    data = monday_api(create_mutation, variables)
    new_item = (data.get("create_item") or {})
    new_item_id = str(new_item.get("id"))
    if not new_item_id:
        raise RuntimeError("Failed to create draw item")

    set_last_draw_mut = """
    mutation SetLastDraw($boardId: ID!, $itemId: ID!, $columnId: String!, $val: JSON!) {
      change_column_value(
        board_id: $boardId,
        item_id: $itemId,
        column_id: $columnId,
        value: $val
      ) { id }
    }
    """
    variables2 = {
        "boardId": str(SERVICING_BOARD_ID),
        "itemId": str(loan_item_id),
        "columnId": str(SERVICING_LAST_DRAW_NUM_COL_ID),
        "val": str(N),
    }
    monday_api(set_last_draw_mut, variables2)

    return {"draw_item_id": new_item_id, "draw_number": N}


# -----------------------------
# Models for new endpoint
# -----------------------------

class SelectionItem(BaseModel):
    item_id: int
    requested_amount: Optional[float] = None

class SelectionRequest(BaseModel):
    # Back-compat simple mode:
    item_ids: Optional[List[int]] = None
    requested_amount: Optional[float] = None
    # New rich mode:
    items: Optional[List[SelectionItem]] = None

# Model for /api/item-names endpoint
class ItemNamesRequest(BaseModel):
    item_ids: List[int]



# -----------------------------
# Routes
# -----------------------------

# Root route
@app.get("/")
def root_health():
    return {"ok": True, "status": "running"}


# Endpoint to fetch item display names (loan numbers preferred)
@app.post("/api/item-names", dependencies=[Depends(require_auth)])
def api_item_names(req: ItemNamesRequest):
    """
    Return display names for servicing items.
    - Always prefer the loan number column (loan_text) if present and non-blank.
    - If missing, fall back to the item name.
    - If still missing, use the raw item id as string.
    - Only a single 'name' field is returned for each item.
    """
    q = """
    query($ids:[ID!], $colId:String!) {
      items(ids:$ids) {
        id
        name
        column_values(ids: [$colId]) { id text }
      }
    }
    """
    try:
        data = monday_api(q, {"ids": [str(i) for i in req.item_ids], "colId": str(SERVICING_LOAN_NUMBER_COL_ID)})
        # Enhanced logging to diagnose missing names
        print(f"DEBUG: SERVICING_LOAN_NUMBER_COL_ID is set to: {SERVICING_LOAN_NUMBER_COL_ID}")
        print(f"DEBUG: Raw data from Monday API for item-names: {json.dumps(data, indent=2)}")

        items = data.get("items") or []
        out = []
        for it in items:
            _id = int(it.get("id"))
            _name = it.get("name") or ""
            cvs = it.get("column_values") or []
            loan_text = ""
            if cvs:
                cv = cvs[0] or {}
                loan_text = cv.get("text") or ""
            # Preferred: loan_text (if present), else item name, else raw id as string
            if loan_text and loan_text.strip():
                preferred = loan_text.strip()
            elif _name and _name.strip():
                preferred = _name.strip()
            else:
                preferred = str(_id)
            # Debug print for each item
            print("DEBUG /api/item-names item parsed:", {"id": _id, "loan_text": loan_text, "name": _name, "preferred": preferred}, flush=True)
            # Return both 'id' and 'name' for each item, with id as string
            out.append({
                "id": str(_id),
                "name": preferred
            })
        
        print(f"DEBUG: Processed names being sent to frontend: {json.dumps(out, indent=2)}")
        return {"items": out}
    except Exception as e:
        print(f"ERROR in api_item_names: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/create-draw", response_model=DrawResponse, dependencies=[Depends(require_auth)])
def api_create_draw(req: DrawRequest):
    try:
        result = create_draw_item(req.loan_item_id, req.loan_number, req.requested_amount)
        return DrawResponse(
            ok=True,
            draw_item_id=result["draw_item_id"],
            loan_item_id=req.loan_item_id,
            loan_number=req.loan_number,
            requested_amount=req.requested_amount,
            draw_number=result["draw_number"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bulk-create", response_model=List[DrawResponse], dependencies=[Depends(require_auth)])
def api_bulk_create(requests_body: List[DrawRequest]):
    results: List[DrawResponse] = []
    for req in requests_body:
        try:
            result = create_draw_item(req.loan_item_id, req.loan_number, req.requested_amount)
            results.append(
                DrawResponse(
                    ok=True,
                    draw_item_id=result["draw_item_id"],
                    loan_item_id=req.loan_item_id,
                    loan_number=req.loan_number,
                    requested_amount=req.requested_amount,
                    draw_number=result["draw_number"],
                )
            )
        except Exception as e:
            results.append(
                DrawResponse(
                    ok=False,
                    loan_item_id=req.loan_item_id,
                    loan_number=req.loan_number,
                    requested_amount=req.requested_amount,
                    error=str(e),
                )
            )
    return results


@app.post("/api/create-from-selection", response_model=List[DrawResponse], dependencies=[Depends(require_auth)])
def api_create_from_selection(sel: SelectionRequest):
    results: List[DrawResponse] = []
    try:
        if sel.items:
            # Rich mode: per-item amount
            for it in sel.items:
                try:
                    loan_number = _get_loan_number_from_item_name(it.item_id)
                    if not loan_number:
                        raise RuntimeError(f"Could not read name for servicing item {it.item_id}")
                    result = create_draw_item(it.item_id, loan_number, it.requested_amount)
                    results.append(
                        DrawResponse(
                            ok=True,
                            draw_item_id=result["draw_item_id"],
                            loan_item_id=it.item_id,
                            loan_number=loan_number,
                            requested_amount=it.requested_amount,
                            draw_number=result["draw_number"],
                        )
                    )
                except Exception as e:
                    results.append(
                        DrawResponse(
                            ok=False,
                            loan_item_id=it.item_id,
                            loan_number=None,
                            requested_amount=it.requested_amount,
                            error=str(e),
                        )
                    )
            return results

        # Back-compat simple mode: one amount for all ids
        if not sel.item_ids:
            raise HTTPException(status_code=400, detail="Provide either 'items' or 'item_ids'.")
        for item_id in sel.item_ids:
            try:
                loan_number = _get_loan_number_from_item_name(item_id)
                if not loan_number:
                    raise RuntimeError(f"Could not read name for servicing item {item_id}")
                result = create_draw_item(item_id, loan_number, sel.requested_amount)
                results.append(
                    DrawResponse(
                        ok=True,
                        draw_item_id=result["draw_item_id"],
                        loan_item_id=item_id,
                        loan_number=loan_number,
                        requested_amount=sel.requested_amount,
                        draw_number=result["draw_number"],
                    )
                )
            except Exception as e:
                results.append(
                    DrawResponse(
                        ok=False,
                        loan_item_id=item_id,
                        loan_number=None,
                        requested_amount=sel.requested_amount,
                        error=str(e),
                    )
                )
        return results
    except HTTPException:
        raise
    except Exception as e:
        # Defensive catch-all
        return [DrawResponse(ok=False, error=str(e))]


# Serve React frontend from dist/
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
if os.path.isdir("dist"):
    app.mount("/static", StaticFiles(directory="dist/assets"), name="static")

    @app.get("/{full_path:path}")
    async def serve_react(full_path: str):
        return FileResponse("dist/index.html")

# Run: uvicorn main:app --reload --port 8000
