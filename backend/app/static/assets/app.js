(() => {
  const TOKEN = document.querySelector('meta[name="jarvis-token"]').content;
  const STT_PROVIDER = (document.querySelector('meta[name="jarvis-stt-provider"]') || {}).content || "mock";
  const authHeaders = { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" };

  function api(path, options = {}) {
    return fetch(path, { ...options, headers: { ...authHeaders, ...(options.headers || {}) } });
  }

  // ---------- Local speech recognition ----------
  // Drop-in stand-in for the browser's SpeechRecognition, backed by
  // /v1/transcribe (local faster-whisper) instead of a cloud service. It
  // deliberately mimics the same start/stop/addEventListener surface and
  // the same `results[i][0].transcript` event shape, so the wake-word
  // state machine below consumes it without caring which one it got.
  //
  // Raw PCM is captured and encoded to WAV here rather than using
  // MediaRecorder: MediaRecorder's WebM chunks aren't independently
  // decodable (only the first carries the container header), so slicing
  // out just the speech portion of a continuous recording doesn't produce
  // a valid file. Raw PCM slices cleanly, which is what makes the
  // pre-roll buffer below possible.
  const VAD = {
    frameMs: 50,
    // Speech must exceed the ambient floor by this factor to count — an
    // absolute threshold alone misfires in a quiet room and goes deaf in
    // a loud one.
    floorMultiplier: 2.5,
    minRms: 0.012,
    silenceMsToEnd: 900,
    minUtteranceMs: 250,
    maxUtteranceMs: 15000,
    // Audio kept from just *before* speech was detected, so the first
    // syllable of "Jarvis" isn't clipped off by the detector's own lag.
    prerollMs: 300,
  };

  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeString = (offset, text) => {
      for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
    };
    writeString(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, "WAVEfmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, samples.length * 2, true);
    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
      const clamped = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
      offset += 2;
    }
    return new Blob([view], { type: "audio/wav" });
  }

  class LocalSpeechRecognition {
    constructor() {
      // Accepted for interface compatibility with SpeechRecognition;
      // continuous listening is simply how this one always works, and
      // there are no interim results from a batch transcriber.
      this.continuous = true;
      this.interimResults = false;
      this.lang = "en-US";
      this.singleShot = false;
      this._listeners = {};
      this._running = false;
      this._stream = null;
      this._audioContext = null;
      this._source = null;
      this._processor = null;
    }

    addEventListener(type, callback) {
      (this._listeners[type] = this._listeners[type] || []).push(callback);
    }

    _emit(type, event) {
      (this._listeners[type] || []).forEach((callback) => callback(event));
    }

    _emitTranscript(text) {
      const result = [{ transcript: text }];
      result.isFinal = true;
      this._emit("result", { resultIndex: 0, results: [result] });
    }

    async start() {
      if (this._running) throw new Error("already started");
      this._running = true;
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      } catch (err) {
        this._running = false;
        this._emit("error", { error: err && err.name === "NotAllowedError" ? "not-allowed" : "audio-capture" });
        this._emit("end", {});
        return;
      }

      this._stream = stream;
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      this._audioContext = audioContext;
      const sampleRate = audioContext.sampleRate;
      const source = audioContext.createMediaStreamSource(stream);
      this._source = source;

      const frameSize = 4096;
      const processor = audioContext.createScriptProcessor(frameSize, 1, 1);
      this._processor = processor;

      const prerollFrames = Math.ceil((VAD.prerollMs / 1000) * sampleRate);
      let preroll = [];
      let prerollLength = 0;
      let speaking = false;
      let utterance = [];
      let utteranceLength = 0;
      let silenceMs = 0;
      let noiseFloor = 0.01;

      const finish = () => {
        const durationMs = (utteranceLength / sampleRate) * 1000;
        const samples = new Float32Array(utteranceLength);
        let offset = 0;
        for (const chunk of utterance) {
          samples.set(chunk, offset);
          offset += chunk.length;
        }
        speaking = false;
        utterance = [];
        utteranceLength = 0;
        silenceMs = 0;
        if (durationMs >= VAD.minUtteranceMs) {
          console.debug(`[JARVIS] captured utterance: ${Math.round(durationMs)}ms, transcribing…`);
          this._transcribe(encodeWav(samples, sampleRate));
        } else {
          console.debug(`[JARVIS] ignored blip: ${Math.round(durationMs)}ms`);
        }
      };

      processor.onaudioprocess = (event) => {
        if (!this._running) return;
        const input = event.inputBuffer.getChannelData(0);
        let sumSquares = 0;
        for (let i = 0; i < input.length; i++) sumSquares += input[i] * input[i];
        const rms = Math.sqrt(sumSquares / input.length);
        const frameMs = (input.length / sampleRate) * 1000;
        const threshold = Math.max(noiseFloor * VAD.floorMultiplier, VAD.minRms);

        if (!speaking) {
          // Track the ambient floor only while nobody is talking, so a long
          // utterance can't drag the threshold up over its own voice.
          noiseFloor = noiseFloor * 0.95 + rms * 0.05;
          const copy = new Float32Array(input);
          preroll.push(copy);
          prerollLength += copy.length;
          while (prerollLength - preroll[0].length >= prerollFrames) {
            prerollLength -= preroll.shift().length;
          }
          if (rms > threshold) {
            console.debug(`[JARVIS] speech detected (rms ${rms.toFixed(4)} > ${threshold.toFixed(4)})`);
            speaking = true;
            silenceMs = 0;
            utterance = preroll.slice();
            utteranceLength = prerollLength;
            preroll = [];
            prerollLength = 0;
          }
          return;
        }

        const copy = new Float32Array(input);
        utterance.push(copy);
        utteranceLength += copy.length;
        silenceMs = rms > threshold ? 0 : silenceMs + frameMs;
        const durationMs = (utteranceLength / sampleRate) * 1000;
        if (silenceMs >= VAD.silenceMsToEnd || durationMs >= VAD.maxUtteranceMs) finish();
      };

      source.connect(processor);
      // ScriptProcessorNode only runs while connected to a destination;
      // a zero-gain node keeps it ticking without echoing the mic to the
      // speakers.
      const mute = audioContext.createGain();
      mute.gain.value = 0;
      processor.connect(mute);
      mute.connect(audioContext.destination);

      this._emit("start", {});
    }

    async _transcribe(blob) {
      try {
        const buffer = await blob.arrayBuffer();
        let binary = "";
        const bytes = new Uint8Array(buffer);
        const CHUNK = 0x8000;
        for (let i = 0; i < bytes.length; i += CHUNK) {
          binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
        }
        const res = await api("/v1/transcribe", {
          method: "POST",
          body: JSON.stringify({ audio_base64: btoa(binary), language: this.lang }),
        });
        if (!res.ok) {
          this._emit("error", { error: "network" });
          return;
        }
        const data = await res.json();
        const text = (data.text || "").trim();
        // Silence that slipped past the VAD transcribes to nothing —
        // that's routine, not a failure worth surfacing.
        if (!text) return;
        this._emitTranscript(text);
        if (this.singleShot) this.stop();
      } catch (_err) {
        this._emit("error", { error: "network" });
      }
    }

    stop() {
      if (!this._running) return;
      this._running = false;
      try {
        if (this._processor) this._processor.disconnect();
        if (this._source) this._source.disconnect();
        if (this._audioContext) this._audioContext.close();
        if (this._stream) this._stream.getTracks().forEach((track) => track.stop());
      } catch (_err) {
        // Teardown races (context already closed, track already ended)
        // aren't worth surfacing — the listener is stopping either way.
      }
      this._processor = null;
      this._source = null;
      this._audioContext = null;
      this._stream = null;
      this._emit("end", {});
    }
  }

  // ---------- System stats ----------
  function fmtBytes(gb) {
    return `${gb.toFixed(1)} GB`;
  }

  async function pollSystemStats() {
    try {
      const res = await api("/v1/system/stats");
      if (!res.ok) return;
      const stats = await res.json();
      document.getElementById("cpu-value").textContent = `${stats.cpu_percent.toFixed(0)}%`;
      document.getElementById("cpu-meter").style.width = `${stats.cpu_percent}%`;
      document.getElementById("ram-value").textContent =
        `${fmtBytes(stats.ram_used_gb)} / ${fmtBytes(stats.ram_total_gb)}`;
      document.getElementById("ram-meter").style.width = `${stats.ram_percent}%`;
      document.getElementById("disk-value").textContent = `${stats.disk_percent.toFixed(0)}%`;
      document.getElementById("gpu-value").textContent = stats.gpu
        ? `${stats.gpu.load_percent.toFixed(0)}%`
        : "N/A";
    } catch (_err) {
      // Transient poll failure — next tick retries, nothing to surface.
    }
  }

  pollSystemStats();
  setInterval(pollSystemStats, 2000);

  // ---------- Local clock ----------
  function updateClock() {
    const now = new Date();
    document.getElementById("clock-time").textContent = now.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    document.getElementById("clock-date").textContent = now.toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
  }

  updateClock();
  setInterval(updateClock, 1000);

  // ---------- Weather (client-side only, no backend involvement) ----------
  const WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Light drizzle", 61: "Light rain", 63: "Rain",
    65: "Heavy rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 95: "Thunderstorm",
  };

  async function loadWeather(lat, lon, place) {
    try {
      const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}` +
        `&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code` +
        `&temperature_unit=fahrenheit`;
      const res = await fetch(url);
      const data = await res.json();
      const cur = data.current;
      document.getElementById("weather-temp").innerHTML = `${cur.temperature_2m.toFixed(1)}&deg;F`;
      document.getElementById("weather-place").textContent = place;
      document.getElementById("weather-desc").textContent = WEATHER_CODES[cur.weather_code] || "—";
      document.getElementById("weather-humidity").textContent = `${cur.relative_humidity_2m}%`;
      document.getElementById("weather-wind").textContent = `${cur.wind_speed_10m.toFixed(1)} m/s`;
    } catch (_err) {
      document.getElementById("weather-place").textContent = "Weather unavailable";
    }
  }

  async function resolvePlaceName(lat, lon) {
    try {
      const res = await fetch(
        `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=en`
      );
      const data = await res.json();
      return data.city || data.locality || data.countryName || "Current location";
    } catch (_err) {
      return "Current location";
    }
  }

  function initWeather() {
    const fallback = { lat: 40.7128, lon: -74.006, place: "New York, NY" };
    if (!navigator.geolocation) {
      loadWeather(fallback.lat, fallback.lon, fallback.place);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const { latitude, longitude } = pos.coords;
        const place = await resolvePlaceName(latitude, longitude);
        loadWeather(latitude, longitude, place);
      },
      () => loadWeather(fallback.lat, fallback.lon, fallback.place),
      { timeout: 5000 }
    );
  }

  initWeather();

  // ---------- Chat ----------
  const chatEl = document.getElementById("chat");
  const orbEl = document.getElementById("orb");
  const statusEl = document.getElementById("status-line");
  const form = document.getElementById("composer");
  const input = document.getElementById("composer-input");
  const sendBtn = document.getElementById("composer-send");
  const clearHistoryBtn = document.getElementById("clear-history");
  const chatToggleBtn = document.getElementById("chat-toggle");

  // Tool names the reasoning model is told it may propose, and the
  // permissions this desktop session is treated as having granted. Mirrors
  // the phone app's demo-mode set (ios/JarvisKit/Sources/JarvisKit/Mocks/
  // MockToolExecutor.swift) — the only tools with real server-side
  // execution in app/api/tools.py. This is a single-user local app sharing
  // one bearer token, so "granted" here just means "this desktop UI
  // exposes this integration," not a separate per-feature consent record.
  const AVAILABLE_TOOLS = [
    "get_current_time",
    "delete_memory",
    "delete_all_memories",
    "get_pc_status",
    "control_home_device",
    "open_website",
    "close_website",
    "launch_app",
    "focus_app",
    "close_app",
    "list_open_windows",
    "take_screenshot",
    "list_files",
    "read_file",
    "write_file",
    "append_file",
    "create_folder",
    "move_file",
    "delete_file",
  ];
  const GRANTED_PERMISSIONS = ["pc_agent", "smart_home", "os_control", "screen_capture", "file_access"];

  // Sites open_website can actually type a search query into (see
  // app/services/browser_automation.py's SEARCHABLE_SITES) — used here only
  // to decide whether a bare "open youtube" (no query yet) should arm the
  // pendingSearchSite follow-up below, not to do any opening ourselves;
  // that's all real server-side browser automation now.
  const SEARCHABLE_SITE_KEYS = ["youtube", "google"];

  // ---------- Conversation memory ----------
  // Full transcript persists in localStorage (per-browser-profile, so it
  // survives closing and reopening the dashboard) so past turns can be
  // scrolled back to. Only the most recent MAX_CONTEXT_TURNS pairs are
  // actually replayed to /v1/reason as context on each new question — an
  // unbounded transcript sent every turn would blow up latency/cost for no
  // benefit, and the long-term "remember this" facts still live in the
  // separate /v1/memories store.
  const HISTORY_KEY = "jarvis.conversation.v1";
  const MAX_STORED_TURNS = 200;
  const MAX_CONTEXT_TURNS = 8;

  function loadHistory() {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (_err) {
      return [];
    }
  }

  function saveHistory() {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(conversationHistory.slice(-MAX_STORED_TURNS)));
    } catch (_err) {
      // Storage unavailable/full — conversation just won't persist across reloads.
    }
  }

  let conversationHistory = loadHistory();

  function addMessage(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = text;
    chatEl.appendChild(div);
    chatEl.scrollTop = chatEl.scrollHeight;
    if (chatEl.classList.contains("collapsed")) chatToggleBtn.classList.add("unread");
    return div;
  }

  for (const turn of conversationHistory) {
    addMessage(turn.role, turn.text);
  }

  clearHistoryBtn.addEventListener("click", () => {
    conversationHistory = [];
    saveHistory();
    chatEl.innerHTML = "";
  });

  // ---------- Chat collapse (hidden by default) ----------
  const CHAT_COLLAPSED_KEY = "jarvis.chatCollapsed.v1";

  function setChatCollapsed(collapsed) {
    chatEl.classList.toggle("collapsed", collapsed);
    chatToggleBtn.setAttribute("aria-expanded", String(!collapsed));
    if (!collapsed) {
      chatToggleBtn.classList.remove("unread");
      chatEl.scrollTop = chatEl.scrollHeight;
    }
    try {
      localStorage.setItem(CHAT_COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch (_err) {
      // Non-fatal — just won't remember the collapsed state across reloads.
    }
  }

  const storedCollapsed = (() => {
    try {
      return localStorage.getItem(CHAT_COLLAPSED_KEY);
    } catch (_err) {
      return null;
    }
  })();
  setChatCollapsed(storedCollapsed === null ? true : storedCollapsed === "1");

  chatToggleBtn.addEventListener("click", () => {
    setChatCollapsed(!chatEl.classList.contains("collapsed"));
  });

  function idleStatusText() {
    return wakeModeEnabled ? "Say “Jarvis” to talk" : "Online";
  }

  // A pending tool-call confirmation (see handleToolCall) that a spoken
  // "yes"/"no" can resolve, not just a click — otherwise a voice-only
  // interaction has no way to ever answer a prompt it never looks at.
  let pendingVoiceConfirmation = null;

  function setPendingVoiceConfirmation(resolver) {
    pendingVoiceConfirmation = resolver;
    statusEl.textContent = 'Say "yes" to confirm, or "no" to cancel';
  }

  function clearPendingVoiceConfirmation() {
    pendingVoiceConfirmation = null;
    if (!orbEl.classList.contains("thinking") && !orbEl.classList.contains("speaking")) {
      statusEl.textContent = idleStatusText();
    }
  }

  // A searchable site (youtube/google) that was just opened with no query
  // yet — the next thing heard becomes the search terms directly (see
  // submitPendingSearch), skipping the model entirely for that step so a
  // "what would you like to search for?" follow-up is fast and reliable.
  let pendingSearchSite = null;
  let pendingSearchSiteTimer = null;

  // Same window as the conversation follow-up (see FOLLOW_UP_WINDOW_MS) —
  // without an expiry, this stayed armed indefinitely, so a later
  // completely unrelated "Jarvis, ..." got silently swallowed as literal
  // search text for whatever site was opened much earlier.
  const PENDING_SEARCH_TIMEOUT_MS = 15000;

  function armPendingSearchSite(siteKey) {
    pendingSearchSite = siteKey;
    clearTimeout(pendingSearchSiteTimer);
    pendingSearchSiteTimer = setTimeout(() => {
      console.info("[JARVIS] pendingSearchSite expired unused:", siteKey);
      pendingSearchSite = null;
    }, PENDING_SEARCH_TIMEOUT_MS);
  }

  function clearPendingSearchSite() {
    pendingSearchSite = null;
    clearTimeout(pendingSearchSiteTimer);
  }

  function setThinking(isThinking) {
    orbEl.classList.toggle("thinking", isThinking);
    if (isThinking) statusEl.textContent = "Thinking…";
    else if (!orbEl.classList.contains("speaking")) statusEl.textContent = idleStatusText();
    sendBtn.disabled = isThinking;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // One-shot "flash" on the orb, restarted per word — forcing a reflow lets
  // the CSS animation re-trigger even if the previous flash hasn't finished.
  function flashOrb() {
    orbEl.classList.remove("tick");
    void orbEl.offsetWidth;
    orbEl.classList.add("tick");
  }

  function setSpeaking(isSpeaking) {
    orbEl.classList.toggle("speaking", isSpeaking);
    if (!isSpeaking) orbEl.classList.remove("tick");
    statusEl.textContent = isSpeaking ? "Speaking…" : idleStatusText();
    sendBtn.disabled = isSpeaking;
    onSpeakingChanged(isSpeaking);
  }

  // Set by speak() whenever real backend audio is playing, so a spoken
  // "stop" (see the wake-word stop-phrase handling below) can actually cut
  // it off instead of just letting it finish.
  let activeAudioEl = null;

  function stopSpeaking() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (activeAudioEl) {
      activeAudioEl.pause();
      activeAudioEl.currentTime = 0;
      activeAudioEl = null;
    }
    setSpeaking(false);
  }

  // Speaks `text` via the browser's own speech synthesis. This is the
  // actual audio path today: the default JARVIS_TTS_PROVIDER=mock backend
  // returns a placeholder byte blob (see app/services/speech.py) that
  // isn't real audio at all, so it can never play — browser TTS is free,
  // works offline, and needs no API key.
  function speakWithBrowserTTS(text) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) {
        resolve();
        return;
      }
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.0;
      utterance.onboundary = () => flashOrb();
      utterance.onend = resolve;
      utterance.onerror = resolve;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    });
  }

  // Tries the backend's /v1/speech/synthesize first (real audio once a
  // provider like ElevenLabs is configured) and only falls back to browser
  // TTS if what comes back isn't actually playable — so this keeps working
  // unchanged once a real backend voice is set up.
  async function speak(text) {
    const words = text.split(/\s+/).filter(Boolean);
    if (words.length === 0) return;

    let audioEl = null;
    let audioUrl = null;
    let playable = false;
    try {
      const res = await api("/v1/speech/synthesize", { method: "POST", body: JSON.stringify({ text }) });
      if (res.ok) {
        const data = await res.json();
        const bytes = Uint8Array.from(atob(data.audio_base64), (c) => c.charCodeAt(0));
        const mime = data.format === "mp3" ? "audio/mpeg" : "audio/wav";
        audioUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
        audioEl = new Audio(audioUrl);
        playable = await new Promise((resolve) => {
          audioEl.addEventListener("loadedmetadata", () => resolve(true), { once: true });
          audioEl.addEventListener("error", () => resolve(false), { once: true });
        });
      }
    } catch (_err) {
      playable = false;
    }

    const hasUsableDuration = playable && Number.isFinite(audioEl.duration) && audioEl.duration > 0;
    if (audioUrl && !hasUsableDuration) URL.revokeObjectURL(audioUrl);

    if (!hasUsableDuration) {
      setSpeaking(true);
      await speakWithBrowserTTS(text);
      setSpeaking(false);
      return;
    }

    const totalMs = audioEl.duration * 1000;
    const totalChars = words.reduce((sum, w) => sum + w.length, 0) || words.length;
    const wordDurationsMs = words.map((w) => Math.max(70, (w.length / totalChars) * totalMs));

    setSpeaking(true);
    activeAudioEl = audioEl;
    audioEl.play().catch(() => {});

    for (const wordMs of wordDurationsMs) {
      if (activeAudioEl !== audioEl) break; // stopSpeaking() cut this off early
      flashOrb();
      await sleep(wordMs);
    }

    activeAudioEl = null;
    setSpeaking(false);
    URL.revokeObjectURL(audioUrl);
  }

  async function ensureSession() {
    try {
      await api("/v1/session", {
        method: "POST",
        body: JSON.stringify({ device_id: "desktop-dashboard", client_version: "0.1.0" }),
      });
    } catch (_err) {
      // Non-fatal — /v1/reason doesn't actually depend on the session_id today.
    }
  }

  // `spokenAnswer` is the model's own pre-execution announcement (e.g.
  // "Opening Google...") — speaking it unconditionally regardless of
  // whether confirmation turns out to be required was the actual bug
  // behind "it doesn't respond, and asking again doesn't help": a
  // voice-only interaction has no way to know a Confirm/Cancel prompt it
  // can't see is sitting there waiting for a spoken "yes". So this
  // function alone decides what gets spoken, only once it actually knows.
  async function handleToolCall(call, requiresConfirmation, messageDiv, spokenAnswer) {
    const proposeRes = await api("/v1/tools/propose", {
      method: "POST",
      body: JSON.stringify({
        tool_name: call.tool_name,
        target: call.target,
        arguments: call.arguments || {},
        granted_permissions: GRANTED_PERMISSIONS,
      }),
    });
    const proposal = await proposeRes.json();

    if (proposal.decision === "deny") {
      if (spokenAnswer) await speak(spokenAnswer);
      addMessage("error", proposal.reason || "That action was denied.");
      return;
    }

    const runExecute = async (confirmed, { speakResult } = {}) => {
      const execRes = await api("/v1/tools/execute", {
        method: "POST",
        body: JSON.stringify({
          tool_name: call.tool_name,
          target: call.target,
          // Body text for write_file/append_file; empty for everything else.
          content: call.content || "",
          arguments: call.arguments || {},
          confirmed,
          granted_permissions: GRANTED_PERMISSIONS,
        }),
      });
      const result = await execRes.json();
      const output = result.output || (execRes.ok ? "Done." : "Failed.");
      addMessage(result.success ? "assistant" : "error", output);
      // Nothing else announces completion out loud for a confirmed action
      // (the pre-execution line already covered the immediate/no-confirm
      // path) — without this, saying "yes" produces total silence.
      if (speakResult) await speak(output);
      // Opening/searching/closing sites is real server-side browser
      // automation now (see app/services/browser_automation.py) — no
      // window.open() or popup-blocker workaround needed here anymore. If
      // a searchable site was opened with no query yet, arm the follow-up
      // so the very next thing said becomes the search terms directly,
      // without needing to say "Jarvis" again or round-trip the model.
      if (result.success && call.tool_name === "open_website") {
        const [siteKey, ...rest] = (call.target || "").trim().toLowerCase().split(/\s+/);
        if (!rest.length && SEARCHABLE_SITE_KEYS.includes(siteKey)) {
          armPendingSearchSite(siteKey);
        }
      }
    };

    const needsConfirmation = proposal.decision !== "allow" || requiresConfirmation;

    if (!needsConfirmation) {
      if (spokenAnswer) await speak(spokenAnswer);
      await runExecute(false);
      return;
    }

    await speak(spokenAnswer ? `${spokenAnswer} Should I go ahead?` : "Should I go ahead?");

    const row = document.createElement("div");
    row.className = "confirm-row";
    row.innerHTML =
      `<span>${proposal.confirmation_summary || `Run "${call.tool_name}"?`}</span>`;
    const accept = document.createElement("button");
    accept.className = "accept";
    accept.textContent = "Confirm";
    const reject = document.createElement("button");
    reject.className = "reject";
    reject.textContent = "Cancel";
    // A click isn't the only valid way to answer this — see
    // pendingVoiceConfirmation below, resolved by the wake-word recognizer
    // hearing "yes"/"no". Without that, a voice-only interaction has no way
    // to ever answer a confirmation prompt it can't see or click.
    const resolveConfirmation = async (confirmed) => {
      row.remove();
      clearPendingVoiceConfirmation();
      if (confirmed) await runExecute(true, { speakResult: true });
    };
    accept.onclick = () => resolveConfirmation(true);
    reject.onclick = () => resolveConfirmation(false);
    row.appendChild(accept);
    row.appendChild(reject);
    messageDiv.appendChild(row);
    setPendingVoiceConfirmation(resolveConfirmation);
  }

  // Answers an armed pendingSearchSite directly against open_website,
  // bypassing sendMessage/the reasoning model entirely — reliability
  // matters more than flexibility for "what would you like to search for?"
  // specifically, since a small local model paraphrasing the query back
  // into a fresh tool call is one more place for it to drop or mangle it.
  async function submitPendingSearch(rawQuery) {
    const siteKey = pendingSearchSite;
    clearPendingSearchSite();
    const query = rawQuery.trim();
    if (!siteKey || !query) return;
    addMessage("user", query);
    const call = { tool_name: "open_website", target: `${siteKey} ${query}`, arguments: {} };
    const placeholder = document.createElement("div");
    await handleToolCall(call, false, placeholder);
    if (wakeModeEnabled) setAwaitingCommand(true);
  }

  // "Jarvis, terminate" — ends the whole app (closes every automated
  // browser tab, then the process itself), not just this conversation.
  // Deliberately not an AI-proposed tool call: matched as a fixed phrase
  // the same way stop-words are, so a small local model's judgment is
  // never in the loop for something this final.
  async function handleTerminate() {
    console.info("[JARVIS] terminate phrase heard — shutting down");
    stopSpeaking();
    statusEl.textContent = "Shutting down…";
    await speak("Shutting down. Goodbye, sir.");
    try {
      await api("/v1/system/terminate", { method: "POST" });
    } catch (_err) {
      // The process is exiting either way — a fetch failure here (the
      // connection resetting mid-request as the server dies) isn't
      // something to surface.
    }
  }

  async function sendMessage(question) {
    addMessage("user", question);
    conversationHistory.push({ role: "user", text: question });
    saveHistory();
    setThinking(true);
    try {
      const priorTurns = conversationHistory.slice(0, -1).slice(-MAX_CONTEXT_TURNS * 2);
      const context = priorTurns.map((turn) => ({
        source: "CONVERSATION_HISTORY",
        label: turn.role === "user" ? "You said earlier in this conversation" : "JARVIS said earlier in this conversation",
        content: turn.text,
      }));
      const res = await api("/v1/reason", {
        method: "POST",
        body: JSON.stringify({ context, question, available_tools: AVAILABLE_TOOLS }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        addMessage("error", detail.detail || "The reasoning provider is unavailable.");
        setThinking(false);
        return;
      }
      const result = await res.json();
      const messageDiv = addMessage("assistant", result.spoken_answer);
      conversationHistory.push({ role: "assistant", text: result.spoken_answer });
      saveHistory();
      setThinking(false);
      if (result.proposed_tool_call) {
        // handleToolCall decides what to actually speak — it may need to
        // say "Should I go ahead?" instead of this verbatim, depending on
        // whether confirmation turns out to be required (see there).
        await handleToolCall(result.proposed_tool_call, result.requires_confirmation, messageDiv, result.spoken_answer);
      } else {
        await speak(result.spoken_answer);
      }
    } catch (_err) {
      addMessage("error", "Could not reach the backend.");
      setThinking(false);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    if (pendingSearchSite) {
      submitPendingSearch(text);
    } else {
      sendMessage(text);
    }
  });

  // ---------- Microphone ----------
  // With JARVIS_STT_PROVIDER=local, transcription is on-device via
  // faster-whisper (LocalSpeechRecognition above) — no cloud service, works
  // offline, and not dependent on the embedded browser engine granting a
  // cloud speech API a microphone. Otherwise this falls back to the
  // browser's own Web Speech API, which for Chromium means Google's cloud.
  // Wake-word mode is off by default and always shown with a visible
  // indicator (the toggle's dot + the orb's amber ring) — see
  // docs/PRIVACY.md's "no background listening" stance; this is an
  // explicit, foreground, opt-in exception to it, not silent always-on
  // recording.
  const micBtn = document.getElementById("mic-btn");
  const wakeToggleBtn = document.getElementById("wake-toggle");
  const browserSpeechImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
  const usingLocalStt = STT_PROVIDER === "local";
  const SpeechRecognitionImpl = usingLocalStt ? LocalSpeechRecognition : browserSpeechImpl;
  console.info("[JARVIS] speech recognition:", usingLocalStt ? "local (faster-whisper)" : "browser Web Speech API");

  let wakeModeEnabled = false;
  let onSpeakingChanged = () => {};
  // Reassigned below once the wake-word recognizer exists — declared here
  // (rather than as a nested function declaration) so submitPendingSearch
  // above can call it safely regardless of sloppy-mode hoisting quirks.
  let setAwaitingCommand = () => {};

  if (!SpeechRecognitionImpl) {
    micBtn.disabled = true;
    micBtn.title = "Speech recognition isn't supported in this browser";
    wakeToggleBtn.disabled = true;
    wakeToggleBtn.title = "Speech recognition isn't supported in this browser";
  } else {
    // ---- Push-to-talk: click, speak once, auto-sends on the final result ----
    const pushToTalk = new SpeechRecognitionImpl();
    pushToTalk.continuous = false;
    pushToTalk.interimResults = true;
    pushToTalk.lang = "en-US";
    // Local transcription is batch, not streaming: there are no interim
    // results to show while speaking, so it has to stop itself once it has
    // the one utterance rather than waiting for an isFinal flag that only
    // the streaming cloud API produces.
    pushToTalk.singleShot = true;

    let pushToTalkActive = false;

    function setPushToTalkActive(isActive) {
      pushToTalkActive = isActive;
      micBtn.classList.toggle("listening", isActive);
      orbEl.classList.toggle("listening", isActive);
      if (isActive) statusEl.textContent = "Listening…";
      else if (!orbEl.classList.contains("thinking") && !orbEl.classList.contains("speaking")) {
        statusEl.textContent = idleStatusText();
      }
    }

    pushToTalk.addEventListener("result", (event) => {
      let transcript = "";
      for (let i = 0; i < event.results.length; i++) transcript += event.results[i][0].transcript;
      input.value = transcript;
      if (event.results[event.results.length - 1].isFinal) pushToTalk.stop();
    });

    pushToTalk.addEventListener("end", () => {
      setPushToTalkActive(false);
      const text = input.value.trim();
      if (text) {
        input.value = "";
        sendMessage(text);
      }
    });

    pushToTalk.addEventListener("error", (event) => {
      setPushToTalkActive(false);
      if (event.error !== "aborted" && event.error !== "no-speech") {
        addMessage("error", `Microphone error: ${event.error}`);
      }
    });

    micBtn.addEventListener("click", () => {
      if (wakeModeEnabled) return; // wake mode already owns the mic
      if (pushToTalkActive) {
        pushToTalk.stop();
        return;
      }
      input.value = "";
      setPushToTalkActive(true);
      try {
        pushToTalk.start();
      } catch (_err) {
        setPushToTalkActive(false);
      }
    });

    // ---- Wake word ("Jarvis") + hands-free follow-up conversation ----
    // Say "Jarvis" (optionally with the request in the same breath, e.g.
    // "Jarvis, what's the weather?") to activate. After JARVIS answers, it
    // stays armed for a short follow-up window so a real back-and-forth
    // doesn't require repeating the wake word every turn.
    const WAKE_RE = /\b(hey\s+)?jarvis\b[,.]?\s*/i;
    // 8s proved too tight in practice — real conversational pauses (reading
    // a response, deciding what to ask next) routinely ran past it, making
    // the follow-up window feel like it never worked at all.
    const FOLLOW_UP_WINDOW_MS = 15000;

    // Said as the awaited follow-up (or right after the wake word), any of
    // these end the conversation instead of being sent to the reasoning
    // model as a literal question. Cuts off Jarvis mid-sentence too, if
    // he's still finishing a reply when you say one of these.
    //
    // Short/ambiguous words ("stop", "cancel") only match the *whole*
    // (post-"Jarvis") utterance — "cancel my reminder for tomorrow" is a
    // real command, not a stop phrase, so it must not match here just
    // because it starts with "cancel". Longer, unambiguous sign-offs are
    // allowed to match with trailing filler ("never mind, thanks").
    const EXACT_ONLY_STOP_PHRASES = ["stop", "cancel", "quiet", "shut up", "shutdown", "shut down"];
    const FLEXIBLE_STOP_PHRASES = [
      "nevermind", "never mind",
      "goodbye", "good bye", "bye",
      "that's all", "thats all", "that is all",
      "thanks", "thank you",
    ];

    function normalizeSpoken(transcript) {
      let normalized = transcript.toLowerCase().replace(/[.,!?]/g, " ").replace(/\s+/g, " ").trim();
      return normalized.replace(/^jarvis\s+/, "").replace(/\s+jarvis$/, "").trim();
    }

    function isStopPhrase(transcript) {
      const normalized = normalizeSpoken(transcript);
      if (EXACT_ONLY_STOP_PHRASES.includes(normalized)) return true;
      return FLEXIBLE_STOP_PHRASES.some((p) => normalized === p || normalized.startsWith(`${p} `));
    }

    // Answers a pending tool-call confirmation (see pendingVoiceConfirmation)
    // the same way clicking Confirm/Cancel would.
    const CONFIRM_YES_PHRASES = ["yes", "yeah", "yep", "yup", "confirm", "do it", "go ahead", "sure", "okay", "ok"];
    const CONFIRM_NO_PHRASES = ["no", "nope", "cancel", "don't", "dont", "never mind"];

    function matchConfirmPhrase(transcript) {
      const normalized = normalizeSpoken(transcript);
      if (CONFIRM_YES_PHRASES.includes(normalized)) return true;
      if (CONFIRM_NO_PHRASES.includes(normalized)) return false;
      return null;
    }

    let wakeRecognition = null;
    let awaitingCommand = false;
    let suppressWakeResults = false;
    let followUpTimer = null;
    let restartWakeTimer = null;

    function extractCommand(transcript) {
      const match = WAKE_RE.exec(transcript);
      if (!match) return null;
      return transcript.slice(match.index + match[0].length).trim();
    }

    setAwaitingCommand = (isAwaiting) => {
      awaitingCommand = isAwaiting;
      orbEl.classList.toggle("listening", isAwaiting);
      orbEl.classList.toggle("wake-armed", wakeModeEnabled && !isAwaiting);
      clearTimeout(followUpTimer);
      if (isAwaiting) {
        statusEl.textContent = "Listening…";
        followUpTimer = setTimeout(() => {
          if (wakeModeEnabled) setAwaitingCommand(false);
        }, FOLLOW_UP_WINDOW_MS);
      } else if (!orbEl.classList.contains("thinking") && !orbEl.classList.contains("speaking")) {
        statusEl.textContent = idleStatusText();
      }
    };

    function safeStartWake() {
      if (!wakeModeEnabled || suppressWakeResults || !wakeRecognition) return;
      try {
        wakeRecognition.start();
      } catch (err) {
        // Most commonly InvalidStateError because it's already running —
        // harmless. Logged so a real failure to (re)start is visible.
        console.debug("[JARVIS] wake recognition start() skipped:", err.message);
      }
    }

    let wakeErrorCount = 0;
    let wakeErrorResetTimer = null;

    function createWakeRecognition() {
      const rec = new SpeechRecognitionImpl();
      rec.continuous = true;
      rec.interimResults = false;
      rec.lang = "en-US";

      rec.addEventListener("start", () => {
        console.info("[JARVIS] wake word recognition started");
      });

      rec.addEventListener("result", (event) => {
        if (suppressWakeResults) return;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (!result.isFinal) continue;
          const transcript = result[0].transcript.trim();
          if (!transcript) continue;
          console.debug("[JARVIS] heard:", transcript);

          if (normalizeSpoken(transcript) === "terminate") {
            handleTerminate();
            return;
          }

          if (pendingVoiceConfirmation) {
            const answer = matchConfirmPhrase(transcript);
            if (answer !== null) {
              console.info("[JARVIS] voice-answered pending confirmation:", answer ? "yes" : "no");
              const resolver = pendingVoiceConfirmation;
              resolver(answer);
              return;
            }
          }

          if (isStopPhrase(transcript)) {
            console.info("[JARVIS] stop phrase heard, ending conversation:", transcript);
            stopSpeaking();
            setAwaitingCommand(false);
            clearPendingSearchSite();
            return;
          }

          if (pendingSearchSite) {
            if (WAKE_RE.test(transcript)) {
              // Saying "Jarvis" again means a fresh command, not an answer
              // to "what would you like to search for?" — drop the stale
              // pending search and fall through to the normal wake-word
              // handling below instead of eating this as literal search text.
              console.info("[JARVIS] fresh wake word heard — dropping stale pendingSearchSite:", pendingSearchSite);
              clearPendingSearchSite();
            } else {
              console.info("[JARVIS] using heard text as search query for", pendingSearchSite, ":", transcript);
              submitPendingSearch(transcript);
              return;
            }
          }

          if (awaitingCommand) {
            setAwaitingCommand(false);
            sendMessage(transcript).then(() => {
              if (wakeModeEnabled) setAwaitingCommand(true);
            });
            return;
          }

          if (WAKE_RE.test(transcript)) {
            const command = extractCommand(transcript);
            if (command) {
              sendMessage(command).then(() => {
                if (wakeModeEnabled) setAwaitingCommand(true);
              });
            } else {
              setAwaitingCommand(true);
            }
            return;
          }
        }
      });

      rec.addEventListener("end", () => {
        console.info("[JARVIS] wake word recognition ended");
        // Chrome ends continuous recognition on its own periodically even
        // mid-session — restart transparently as long as wake mode is on.
        if (wakeModeEnabled && !suppressWakeResults) {
          clearTimeout(restartWakeTimer);
          restartWakeTimer = setTimeout(safeStartWake, 250);
        }
      });

      rec.addEventListener("error", (event) => {
        console.warn("[JARVIS] wake word recognition error:", event.error);
        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          addMessage("error", "Microphone access was denied — wake word mode needs it. Check the site's microphone permission (the icon in the address bar) and try again.");
          setWakeMode(false);
          return;
        }
        if (event.error === "no-speech" || event.error === "aborted") return; // expected, not a real failure

        wakeErrorCount += 1;
        clearTimeout(wakeErrorResetTimer);
        wakeErrorResetTimer = setTimeout(() => {
          wakeErrorCount = 0;
        }, 15000);
        if (wakeErrorCount >= 4) {
          addMessage(
            "error",
            `Wake word listening keeps failing ("${event.error}") — check your microphone and internet connection (speech recognition needs both), or try toggling Wake Word off and back on.`
          );
          wakeErrorCount = 0;
        }
      });

      return rec;
    }

    function setWakeMode(enabled) {
      wakeModeEnabled = enabled;
      wakeToggleBtn.setAttribute("aria-pressed", String(enabled));
      micBtn.disabled = enabled;
      micBtn.title = enabled ? "Wake word mode is listening — no need to press this" : "Speak";
      setAwaitingCommand(false);
      orbEl.classList.toggle("wake-armed", enabled);
      statusEl.textContent = idleStatusText();

      if (enabled) {
        console.info("[JARVIS] wake word mode enabled");
        if (navigator.permissions && navigator.permissions.query) {
          navigator.permissions
            .query({ name: "microphone" })
            .then((status) => console.info("[JARVIS] microphone permission state:", status.state))
            .catch(() => {});
        }
        if (!wakeRecognition) wakeRecognition = createWakeRecognition();
        safeStartWake();
      } else {
        console.info("[JARVIS] wake word mode disabled");
        clearTimeout(followUpTimer);
        clearTimeout(restartWakeTimer);
        orbEl.classList.remove("wake-armed", "listening");
        if (wakeRecognition) {
          try {
            wakeRecognition.stop();
          } catch (_err) {
            /* not running */
          }
        }
      }
    }

    onSpeakingChanged = (isSpeaking) => {
      if (!wakeModeEnabled) return;
      // Pause wake listening while JARVIS is talking so it doesn't pick up
      // (and react to) its own voice coming out of the speakers.
      suppressWakeResults = isSpeaking;
      if (isSpeaking) {
        clearTimeout(restartWakeTimer);
        try {
          wakeRecognition.stop();
        } catch (_err) {
          /* not running */
        }
      } else {
        setTimeout(() => {
          suppressWakeResults = false;
          safeStartWake();
        }, 500);
      }
    };

    wakeToggleBtn.addEventListener("click", () => setWakeMode(!wakeModeEnabled));
  }

  ensureSession();
})();
