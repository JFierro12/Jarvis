import importlib.util
import json
from pathlib import Path

from app.models.runtime import TurnResponse

ROOT = Path(__file__).resolve().parents[2]


def test_schema_export_and_python_fixture():
    spec = importlib.util.spec_from_file_location("export_contract", ROOT / "scripts/export_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.contract() == json.loads((ROOT / "contracts/runtime-v2.json").read_text())
    payload = json.loads((ROOT / "contracts/turn-response.json").read_text())
    assert TurnResponse.model_validate(payload).action_id == "action-1"


def test_swift_and_javascript_use_same_fixture():
    original = (ROOT / "contracts/turn-response.json").read_bytes()
    swift = ROOT / "ios/JarvisKit/Tests/JarvisKitTests/Fixtures/turn-response.json"
    assert swift.read_bytes() == original
