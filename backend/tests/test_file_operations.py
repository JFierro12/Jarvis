import pytest

from app.services.file_operations import (
    FileOperationError,
    append_file,
    create_folder,
    list_files,
    move_file,
    read_file,
    resolve_within_allowed,
    write_file,
)


@pytest.fixture()
def sandbox(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "private.txt").write_text("classified", encoding="utf-8")
    return allowed, secret, str(allowed)


# --- Security boundary: these matter more than the happy paths ---


def test_rejects_parent_directory_traversal(sandbox):
    _allowed, _secret, roots = sandbox
    with pytest.raises(FileOperationError, match="outside"):
        resolve_within_allowed("../secret/private.txt", roots)


def test_rejects_absolute_path_outside_allowed_roots(sandbox):
    _allowed, secret, roots = sandbox
    with pytest.raises(FileOperationError, match="outside"):
        resolve_within_allowed(str(secret / "private.txt"), roots)


def test_rejects_symlink_escaping_allowed_root(sandbox, tmp_path):
    allowed, secret, roots = sandbox
    link = allowed / "shortcut"
    try:
        link.symlink_to(secret, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privileges not available here")
    # The link itself lives inside the permitted root; only resolving it
    # reveals that it points out of bounds.
    with pytest.raises(FileOperationError, match="outside"):
        resolve_within_allowed(str(link / "private.txt"), roots)


def test_refuses_everything_when_no_folders_are_permitted():
    with pytest.raises(FileOperationError, match="No folders are permitted"):
        resolve_within_allowed("notes.txt", "")


def test_bare_name_resolves_inside_first_permitted_root(sandbox):
    allowed, _secret, roots = sandbox
    assert resolve_within_allowed("notes.txt", roots) == (allowed / "notes.txt").resolve()


# --- Behaviour ---


def test_write_then_read_roundtrip(sandbox):
    _allowed, _secret, roots = sandbox
    write_file("notes.txt", "first line", roots)
    assert "first line" in read_file("notes.txt", roots)


def test_write_refuses_to_clobber_existing_file(sandbox):
    _allowed, _secret, roots = sandbox
    write_file("notes.txt", "original", roots)
    with pytest.raises(FileOperationError, match="already exists"):
        write_file("notes.txt", "replacement", roots)
    # The original content must survive the refused overwrite.
    assert "original" in read_file("notes.txt", roots)


def test_append_adds_without_losing_existing_content(sandbox):
    _allowed, _secret, roots = sandbox
    write_file("notes.txt", "first", roots)
    append_file("notes.txt", "second", roots)
    text = read_file("notes.txt", roots)
    assert "first" in text and "second" in text


def test_append_creates_file_when_missing(sandbox):
    _allowed, _secret, roots = sandbox
    append_file("fresh.txt", "hello", roots)
    assert "hello" in read_file("fresh.txt", roots)


def test_move_renames_instead_of_overwriting_on_collision(sandbox):
    allowed, _secret, roots = sandbox
    write_file("a.txt", "source", roots)
    write_file("b.txt", "destination", roots)
    result = move_file("a.txt to b.txt", roots)
    assert "b (2).txt" in result
    # The pre-existing file must be untouched.
    assert "destination" in read_file("b.txt", roots)
    assert "source" in read_file("b (2).txt", roots)
    assert not (allowed / "a.txt").exists()


def test_move_into_folder_keeps_filename(sandbox):
    _allowed, _secret, roots = sandbox
    write_file("report.txt", "data", roots)
    create_folder("archive", roots)
    move_file("report.txt to archive", roots)
    assert "report.txt" in list_files("archive", roots)


def test_move_refuses_destination_outside_allowed_roots(sandbox):
    _allowed, secret, roots = sandbox
    write_file("leak.txt", "data", roots)
    with pytest.raises(FileOperationError, match="outside"):
        move_file(f"leak.txt to {secret / 'leak.txt'}", roots)


def test_read_missing_file_reports_clearly(sandbox):
    _allowed, _secret, roots = sandbox
    with pytest.raises(FileOperationError, match="does not exist"):
        read_file("nope.txt", roots)


def test_list_files_shows_folders_and_files(sandbox):
    _allowed, _secret, roots = sandbox
    create_folder("sub", roots)
    write_file("top.txt", "x", roots)
    listing = list_files(".", roots)
    assert "sub/" in listing and "top.txt" in listing


def test_read_is_truncated_for_very_large_files(sandbox):
    _allowed, _secret, roots = sandbox
    write_file("big.txt", "x" * 10000, roots)
    assert "truncated" in read_file("big.txt", roots)
