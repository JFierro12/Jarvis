# JARVIS desktop app (Windows)

Packages the FastAPI backend + dashboard UI (`backend/app/`) into a Windows
app that runs 24/7 in the system tray, with a real local LLM (via Ollama) as
the reasoning provider instead of a cloud API call.

**This must be built on the Windows machine itself.** PyInstaller does not
cross-compile — running `build_windows.bat` from macOS or Linux will not
produce a `.exe`.

## Setup (on the Windows laptop, e.g. the Ryzen 9 / RTX 4070 machine)

1. **Install Ollama** for Windows: https://ollama.com/download

2. **Pull a model.** The backend defaults to `qwen2.5:14b-instruct-q4_K_M`
   (~9GB VRAM, fits comfortably on a 12GB RTX 4070 with headroom for
   context). Confirm the exact current tag before pulling — model tags on
   Ollama's registry can change:
   ```
   ollama pull qwen2.5:14b-instruct-q4_K_M
   ```
   If that's too heavy or slow, `llama3.1:8b-instruct-q4_K_M` is a lighter
   fallback — set `JARVIS_LOCAL_LLM_MODEL` accordingly (see below).

3. **Configure the backend.** Copy `backend\.env.example` to `backend\.env`
   and set:
   ```
   JARVIS_REASONING_PROVIDER=local
   JARVIS_LOCAL_LLM_MODEL=qwen2.5:14b-instruct-q4_K_M
   ```
   `JARVIS_LOCAL_LLM_BASE_URL` defaults to `http://localhost:11434` (Ollama's
   default) and shouldn't need changing unless Ollama is configured
   differently.

4. **Sanity-check before packaging.** Run the backend directly first and
   confirm a chat message actually gets answered by the local model:
   ```
   cd backend
   pip install -r requirements.txt
   uvicorn app.main:app --host 127.0.0.1 --port 8756
   ```
   Open `http://127.0.0.1:8756/` and send a message. If it errors, check
   Ollama is running (`ollama list`) and the model name matches exactly.

5. **Build the .exe:**
   ```
   cd desktop
   build_windows.bat
   ```
   Output: `desktop\dist\JARVIS\JARVIS.exe`.

6. **Run it.** Launching `JARVIS.exe` opens the dashboard in its own window
   and adds a tray icon. Closing the window minimizes it to the tray rather
   than quitting — that's what makes it stay running in the background. Use
   the tray icon's **Start with Windows** toggle to have it launch
   automatically at login (off by default).

## What's not included yet

Voice input/output, live camera/vision, and wake word are not part of this
build — the dashboard's camera widget is intentionally a static "off" state.
See the main repo's `docs/ROADMAP.md` for what's implemented vs. planned.
