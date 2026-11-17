"""Export canonical runtime schemas without loading application configuration."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.runtime import (
    Capability,
    Enrollment,
    Invitation,
    SessionCredentials,
    TurnRequest,
    TurnResponse,
)


def contract():
    models = (Capability, Enrollment, Invitation, SessionCredentials, TurnRequest, TurnResponse)
    return {"contract_version": "2", "models": {m.__name__: m.model_json_schema() for m in models}}


if __name__ == "__main__":
    directory = ROOT / "contracts"
    directory.mkdir(exist_ok=True)
    path = directory / "runtime-v2.json"
    text = json.dumps(contract(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if path.read_text(encoding="utf-8") != text:
            raise SystemExit("Contract drift: run scripts/export_contract.py")
    else:
        path.write_text(text, encoding="utf-8")
