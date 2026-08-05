from fastapi import Depends, FastAPI, HTTPException, status

from app.commands import COMMAND_DEFINITIONS, execute_command
from app.ratelimit import enforce_rate_limit
from app.schemas import CommandResponse, PCStatusResponse
from app.security import require_pairing_token
from app.status import collect_status

app = FastAPI(title="JARVIS PC Agent", version="0.1.0")


@app.get("/status", response_model=PCStatusResponse, dependencies=[Depends(enforce_rate_limit)])
def get_status(_token: str = Depends(require_pairing_token)) -> PCStatusResponse:
    return collect_status()


@app.post("/commands/{command_id}", response_model=CommandResponse, dependencies=[Depends(enforce_rate_limit)])
def run_command(command_id: str, _token: str = Depends(require_pairing_token)) -> CommandResponse:
    if command_id not in COMMAND_DEFINITIONS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown command: {command_id}")
    try:
        output = execute_command(command_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return CommandResponse(command_id=command_id, success=True, output=output)
