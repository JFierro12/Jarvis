import subprocess
from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class CommandDefinition:
    command_id: str
    description: str
    argv: List[str]


# The mobile app and backend request a command *ID*, never a raw command
# string (spec 19/pc-agent security requirements). Argv lists are fixed and
# never built from request input, so there is no injection surface here.
COMMAND_DEFINITIONS: Dict[str, CommandDefinition] = {
    d.command_id: d
    for d in [
        CommandDefinition("lock_computer", "Locks the screen.", ["pmset", "displaysleepnow"]),
        CommandDefinition("launch_vscode", "Launches Visual Studio Code.", ["open", "-a", "Visual Studio Code"]),
        CommandDefinition(
            "start_dev_server", "Starts the configured dev server.", ["echo", "start_dev_server: configure me in commands.py"]
        ),
        CommandDefinition(
            "stop_dev_server", "Stops the configured dev server.", ["echo", "stop_dev_server: configure me in commands.py"]
        ),
        CommandDefinition(
            "run_test_script", "Runs the configured test script.", ["echo", "run_test_script: configure me in commands.py"]
        ),
    ]
}


def run_subprocess(argv: List[str]) -> subprocess.CompletedProcess:
    """The only place this service shells out. Always a fixed argv list
    (never `shell=True`, never string-interpolated), so there is nothing for
    a caller to inject into."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)


def execute_command(command_id: str, runner=None) -> str:
    definition = COMMAND_DEFINITIONS.get(command_id)
    if definition is None:
        raise KeyError(command_id)
    # Resolved at call time (not as a default-argument value) so tests can
    # monkeypatch `run_subprocess` at the module level without needing to
    # thread a fake runner through every caller, including the HTTP endpoint.
    active_runner = runner or run_subprocess
    result = active_runner(definition.argv)
    if result.returncode != 0:
        raise RuntimeError(f"{command_id} failed: {result.stderr.strip()}")
    return result.stdout.strip() or f"{command_id} completed"
