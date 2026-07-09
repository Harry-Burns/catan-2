from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn
import requests

from display.web.api_adapter import *

class Input(BaseModel):
    board: ExtendedBoardState

current_board = ExtendedBoardState(
    hexes=[HexState(id=i, resource='desert', number=None, hasRobber=False) for i in range(19)],
)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# --- FastAPI Endpoints ---
@app.get("/board", response_class=HTMLResponse)
async def display_board():
    try:
        return FileResponse("display/web/catan_board.html")
    except RuntimeError as e:
        print(f"Error serving HTML file: {e}")
        raise HTTPException(status_code=500, detail="HTML file not found or error reading it.")

@app.post("/state")
async def set_board(input: Input):
    global current_board
    current_board = input.board
    return input.board

@app.get("/state")
async def get_board():
    return current_board 

if __name__ == "__main__":
    uvicorn.run(app, host='127.0.0.1', port=8000)

# --- To run this application (from your terminal, in the project directory): ---
# uvicorn main:app --reload
# Then: http://127.0.0.1:8000