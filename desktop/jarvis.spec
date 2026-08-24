# PyInstaller spec for the JARVIS desktop app. Build ON Windows only:
#   cd desktop && pyinstaller jarvis.spec
# (PyInstaller cannot cross-compile - a build run on macOS/Linux produces a
# macOS/Linux binary, not a .exe.)
import os

BACKEND_DIR = os.path.join(SPECPATH, "..", "backend")
STATIC_DIR = os.path.join(BACKEND_DIR, "app", "static")

block_cipher = None

a = Analysis(
    [os.path.join(BACKEND_DIR, "app", "desktop_main.py")],
    pathex=[BACKEND_DIR],
    binaries=[],
    datas=[(STATIC_DIR, "app/static")],
    hiddenimports=[
        # uvicorn dynamically imports its protocol backends; PyInstaller's
        # static analysis doesn't see them, so they must be listed here or
        # the packaged .exe fails at startup with "server not started".
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
        "uvicorn.loops.auto",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JARVIS",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

# --onedir, not --onefile: a --onefile build re-extracts itself into a temp
# dir on every launch, adding a multi-second delay each time - unacceptable
# for something meant to auto-start and stay running 24/7.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="JARVIS",
)
