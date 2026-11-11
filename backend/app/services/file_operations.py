import shutil
from pathlib import Path
from typing import List, Optional

# Reading a whole file into a spoken answer is never useful and can blow up
# the model's context, so reads are capped and explicitly marked truncated.
MAX_READ_CHARS = 4000
MAX_LISTING_ENTRIES = 200


class FileOperationError(Exception):
    pass


def _allowed_roots(allowed_folders: str) -> List[Path]:
    roots = []
    for raw in allowed_folders.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            roots.append(Path(candidate).expanduser().resolve())
        except OSError:
            continue
    return roots


def resolve_within_allowed(path_str: str, allowed_folders: str) -> Path:
    """Resolve a requested path and prove it stays inside a permitted root.

    `.resolve()` does the security work: it collapses `..` segments *and*
    follows symlinks, so both "escape by traversal" and "escape by symlink
    pointing elsewhere" end up compared as their real destination. Checking
    the pre-resolution string instead would let either through.
    """
    roots = _allowed_roots(allowed_folders)
    if not roots:
        raise FileOperationError(
            "No folders are permitted for file access — set JARVIS_ALLOWED_FOLDERS to enable file operations"
        )

    cleaned = (path_str or "").strip().strip('"').strip("'")
    if not cleaned:
        raise FileOperationError("No path given")

    candidate = Path(cleaned).expanduser()
    if not candidate.is_absolute():
        # A bare name like "notes.txt" is interpreted inside the first
        # permitted folder rather than the process's working directory,
        # which the user never chose and can't see.
        candidate = roots[0] / candidate

    resolved = candidate.resolve()
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise FileOperationError(f'"{cleaned}" is outside the folders JARVIS is allowed to access')


def _non_colliding(path: Path) -> Path:
    """Pick a free name instead of overwriting: "notes.txt" -> "notes (2).txt"."""
    if not path.exists():
        return path
    for counter in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileOperationError(f"Could not find a free filename near {path.name}")


def list_files(path_str: str, allowed_folders: str) -> str:
    roots = _allowed_roots(allowed_folders)
    if not path_str.strip():
        if not roots:
            raise FileOperationError("No folders are permitted for file access")
        return "Permitted folders: " + ", ".join(str(root) for root in roots)

    folder = resolve_within_allowed(path_str, allowed_folders)
    if not folder.exists():
        raise FileOperationError(f'"{folder}" does not exist')
    if not folder.is_dir():
        raise FileOperationError(f'"{folder}" is a file, not a folder')

    entries = []
    for entry in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        entries.append(f"{entry.name}/" if entry.is_dir() else entry.name)
        if len(entries) >= MAX_LISTING_ENTRIES:
            entries.append(f"… (more than {MAX_LISTING_ENTRIES} items)")
            break
    return ", ".join(entries) if entries else "(empty folder)"


def read_file(path_str: str, allowed_folders: str) -> str:
    path = resolve_within_allowed(path_str, allowed_folders)
    if not path.exists():
        raise FileOperationError(f'"{path.name}" does not exist')
    if path.is_dir():
        raise FileOperationError(f'"{path.name}" is a folder — use list_files for that')
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise FileOperationError(f"Could not read {path.name}: {exc}") from exc
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n… (truncated at {MAX_READ_CHARS} characters)"
    return text or "(file is empty)"


def write_file(path_str: str, content: str, allowed_folders: str) -> str:
    """Create a new file. Refuses to clobber an existing one — silently
    replacing a file the user forgot about is exactly the kind of
    unrecoverable surprise a voice command shouldn't be able to cause;
    append_file or an explicit different name covers the real cases."""
    path = resolve_within_allowed(path_str, allowed_folders)
    if path.exists():
        raise FileOperationError(f'"{path.name}" already exists — append to it or choose another name')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise FileOperationError(f"Could not write {path.name}: {exc}") from exc
    return f"Created {path.name} in {path.parent}."


def append_file(path_str: str, content: str, allowed_folders: str) -> str:
    path = resolve_within_allowed(path_str, allowed_folders)
    if path.is_dir():
        raise FileOperationError(f'"{path.name}" is a folder')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        separator = "" if (not existing or existing.endswith("\n")) else "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(separator + content + "\n")
    except OSError as exc:
        raise FileOperationError(f"Could not append to {path.name}: {exc}") from exc
    return f"Added to {path.name}."


def create_folder(path_str: str, allowed_folders: str) -> str:
    path = resolve_within_allowed(path_str, allowed_folders)
    if path.exists():
        return f"{path.name} already exists."
    try:
        path.mkdir(parents=True)
    except OSError as exc:
        raise FileOperationError(f"Could not create {path.name}: {exc}") from exc
    return f"Created folder {path.name} in {path.parent}."


def move_file(target: str, allowed_folders: str) -> str:
    """target is "<source> to <destination>" — both ends are resolved and
    permission-checked independently, so moving *out* of a permitted folder
    is refused just as firmly as moving in from outside one."""
    lowered = target.lower()
    marker = lowered.rfind(" to ")
    if marker == -1:
        raise FileOperationError('Say it as "<source> to <destination>"')
    source_str, destination_str = target[:marker], target[marker + 4 :]

    source = resolve_within_allowed(source_str, allowed_folders)
    destination = resolve_within_allowed(destination_str, allowed_folders)
    if not source.exists():
        raise FileOperationError(f'"{source.name}" does not exist')

    if destination.is_dir():
        destination = destination / source.name
    destination = _non_colliding(destination)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    except OSError as exc:
        raise FileOperationError(f"Could not move {source.name}: {exc}") from exc
    return f"Moved {source.name} to {destination.parent} as {destination.name}."


def delete_file(path_str: str, allowed_folders: str) -> str:
    """Sends to the Recycle Bin rather than unlinking, so a misheard
    filename stays recoverable (Section 6's "practical undo")."""
    path = resolve_within_allowed(path_str, allowed_folders)
    if not path.exists():
        return f'"{path.name}" does not exist.'
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise FileOperationError("send2trash is not installed — refusing to delete permanently") from exc
    try:
        send2trash(str(path))
    except Exception as exc:
        raise FileOperationError(f"Could not delete {path.name}: {exc}") from exc
    return f"Moved {path.name} to the Recycle Bin."
