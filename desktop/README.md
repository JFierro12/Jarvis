# JARVIS desktop app (Windows)

Packages the FastAPI backend + dashboard UI (`backend/app/`) into a Windows
app that runs 24/7 in the system tray, with a real local LLM (via Ollama) as
the reasoning provider instead of a cloud API call. The dashboard has voice
input (wake word + push-to-talk), voice output, and real browser automation
(JARVIS can open/search/close sites and search-typing on your behalf in a
dedicated, JARVIS-controlled Chrome window — see "Browser automation" below).

**This must be built on the Windows machine itself.** PyInstaller does not
cross-compile — running `build_windows.bat` from macOS or Linux will not
produce a `.exe`.

## Setup (on the Windows laptop, e.g. the Ryzen 9 / RTX 4070 machine)

1. **Install Ollama** for Windows: https://ollama.com/download

2. **Pull a model.** The backend defaults to `qwen2.5:14b-instruct-q4_K_M`
   (~9GB VRAM — fits an RTX 4070 desktop's 12GB, but is tight on the 8GB
   RTX 4070 *Laptop* GPU). Confirm the exact current tag before pulling —
   model tags on Ollama's registry can change:
   ```
   ollama pull qwen2.5:14b-instruct-q4_K_M
   ```
   On an 8GB card, `llama3.1:8b-instruct-q4_K_M` (~4.9GB) is a safer fit —
   set `JARVIS_LOCAL_LLM_MODEL` accordingly (see below).

3. **Install Playwright's browser** (needed for the "open a website" /
   browser-automation voice commands — see below):
   ```
   cd backend
   pip install -e ".[dev]"
   playwright install chromium
   ```
   This downloads a dedicated ~150MB Chromium build to
   `%LOCALAPPDATA%\ms-playwright\` — separate from your regular Chrome, and
   what JARVIS actually drives. Skipping this step means `open_website` /
   `close_website` fail with a "browser not found" error, but everything
   else (chat, voice, tools that don't touch a browser) still works fine.

4. **Configure the backend.** Copy `backend\.env.example` to `backend\.env`
   and set:
   ```
   JARVIS_REASONING_PROVIDER=local
   JARVIS_LOCAL_LLM_MODEL=qwen2.5:14b-instruct-q4_K_M
   ```
   `JARVIS_LOCAL_LLM_BASE_URL` defaults to `http://localhost:11434` (Ollama's
   default) and shouldn't need changing unless Ollama is configured
   differently. Also set `JARVIS_TTS_PROVIDER=elevenlabs` (plus the API
   key/voice ID) if you have a cloned voice — otherwise JARVIS speaks via
   the browser's built-in text-to-speech, which works but sounds robotic.

5. **Sanity-check before packaging.** Run the backend directly first and
   confirm a chat message actually gets answered by the local model:
   ```
   cd backend
   uvicorn app.main:app --host 127.0.0.1 --port 8756
   ```
   Open `http://127.0.0.1:8756/` and send a message. If it errors, check
   Ollama is running (`ollama list`) and the model name matches exactly.

6. **Build the .exe:**
   ```
   cd desktop
   build_windows.bat
   ```
   Output: `desktop\dist\JARVIS\JARVIS.exe`. The script copies
   `backend\.env` into `dist\JARVIS\.env` automatically — do this build step
   again (or manually re-copy `.env`) any time you change the backend
   config, since a rebuild wipes `dist\JARVIS` and starts fresh.

7. **Run it.** Launching `JARVIS.exe` opens the dashboard in its own window
   and adds a tray icon. Closing the window minimizes it to the tray rather
   than quitting — that's what makes it stay running in the background. Use
   the tray icon's **Start with Windows** toggle to have it launch
   automatically at login (off by default), or say "Jarvis, terminate" to
   quit the whole app (and close any open automated browser tabs) from
   voice.

## Browser automation

Say "Jarvis, open YouTube" (or Google, Netflix, Peacock, Hulu, Disney+,
Gmail, Google Calendar) and JARVIS opens it in its own dedicated,
JARVIS-controlled Chrome window — separate from your regular browser. For
YouTube and Google specifically, if you don't say what to search for yet,
JARVIS asks — the next thing you say gets typed into the real search box
and submitted. "Jarvis, close YouTube" closes that tab again.

For every other site, JARVIS only ever opens the homepage — it cannot
click, search, or press play inside them on your behalf. That's not just a
technical limit: automating further into those platforms' own UIs likely
crosses their Terms of Service, especially for DRM-gated streaming
playback, so this is a deliberate boundary, not a missing feature.

## Debugging a packaged build

`JARVIS.exe` is a windowed app (no console), so nothing prints anywhere
visible by default. Errors — including a full traceback for any failed
request — are logged to `jarvis.log` next to the .exe instead. Check there
first if something that worked in `uvicorn app.main:app` dev mode doesn't
work in the packaged build.

## What's not included yet

Live camera/vision is not part of this build — the dashboard's camera
widget is intentionally a static "off" state. `shutdown_pc`/`lock_pc` are
defined in the tool registry but have no real implementation on either
platform yet. See the main repo's `docs/ROADMAP.md` for what's implemented
vs. planned more broadly.
