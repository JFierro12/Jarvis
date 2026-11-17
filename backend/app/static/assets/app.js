(() => {
  let credentials = null;
  let conversationId = "";
  let activeActionId = "";
  let turnEpoch = 0;
  async function cancelCurrentTurn() {
    turnEpoch += 1;
    stopSpeaking();
    clearPendingVoiceConfirmation();
    if (activeActionId) {
      const actionId = activeActionId;
      activeActionId = "";
      const response = await api(`/v1/actions/${actionId}/cancel`, { method: "POST" });
      if (!response.ok) addMessage("error", "Execution has already started and may not be interruptible.");
    }
  }
  const STT_PROVIDER = (document.querySelector('meta[name="jarvis-stt-provider"]') || {}).content || "mock";

  function api(path, options = {}) {
    if (!credentials) return Promise.reject(new Error("Open the native desktop app or pair this device first."));
    return fetch(path, { ...options, headers: {
      Authorization: `Bearer ${credentials.access_token}`, "Content-Type": "application/json",
      ...(options.headers || {}),
    } });
  }

  window.jarvisBootstrap = async (session) => {
    credentials = session;
    await ensureSession();
  };

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

  // Shared with speak()/setSpeaking() below. While the assistant's reply is
  // playing, and for a short tail afterwards covering speaker decay, captured
  // audio is discarded instead of transcribed -- otherwise continuous
  // listening hears the assistant's own voice, treats it as a new user turn,
  // and can answer itself in a loop. Browser echo cancellation reduces this
  // but is not reliable enough with external speakers to depend on.
  const speechGate = new JarvisVoice.SpeechGate({ tailMs: 400 });

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
      // Invalidated on stop(), so an in-flight transcription cannot post a
      // turn after the user has stopped listening.
      this._turns = new JarvisVoice.TurnSequencer();
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
        // The saved microphone is a preference, not a requirement: with an
        // `exact` deviceId, unplugging that microphone breaks voice input
        // permanently instead of falling back to the default device.
        const savedDeviceId = JarvisVoice.loadSavedDeviceId(window.localStorage);
        stream = await navigator.mediaDevices.getUserMedia(
          JarvisVoice.audioConstraints(savedDeviceId)
        );
      } catch (err) {
        this._running = false;
        // "Access denied" shown to someone who has no microphone at all
        // sends them to a permission panel that is already correct.
        const classified = JarvisVoice.classifyMicError(err);
        this._emit("error", { error: classified.code, detail: classified.message });
        this._emit("end", {});
        return;
      }

      this._stream = stream;
      // Permission has now definitely been granted, which is the moment the
      // browser starts exposing real device labels. Tell the device picker so
      // it can replace its "allow access to choose a device" hint with the
      // actual list, rather than waiting for a reload.
      try {
        window.dispatchEvent(new Event("jarvis-mic-permission-granted"));
      } catch (_err) {
        // Event construction is not worth failing a capture over.
      }
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

        // Self-transcription guard. Anything heard while the assistant is
        // speaking is presumed to be its own voice: drop the frame, abandon
        // any utterance in progress, and do NOT fold the level into the
        // noise floor -- learning the ambient floor from the assistant's own
        // playback would desensitise the detector to the user for seconds
        // afterwards.
        if (speechGate.isSuppressed(performance.now())) {
          if (speaking) {
            speaking = false;
            utterance = [];
            utteranceLength = 0;
            silenceMs = 0;
          }
          preroll = [];
          prerollLength = 0;
          return;
        }

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
      // Transcription is asynchronous, so a result can arrive after the user
      // stopped listening or after they started a new utterance. Delivering
      // it then posts a turn they did not just ask for. The generation is
      // captured before the request and checked after it.
      const generation = this._turns.begin();
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
        if (!this._turns.accept(generation)) {
          console.debug("[JARVIS] dropped a stale transcription from a cancelled turn");
          return;
        }
        this._emitTranscript(text);
        if (this.singleShot) this.stop();
      } catch (_err) {
        this._emit("error", { error: "network" });
      }
    }

    stop() {
      if (!this._running) return;
      this._running = false;
      // Anything still being transcribed belongs to a turn the user ended.
      this._turns.invalidate();
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

  // ---------- Weather (via the backend, which owns location) ----------
  //
  // This used to resolve location in the browser and, whenever geolocation
  // failed for any reason, fall back to hardcoded coordinates for New York
  // while displaying a real temperature labelled "New York, NY". A user in
  // Seattle saw a confident, specific, wrong answer with nothing marking it
  // as a guess. It also sent precise coordinates to api.bigdatacloud.net,
  // a third party documented nowhere in the project.
  //
  // Now the browser's only job is to offer a position when the user has
  // granted permission. Deciding *where the user is* belongs to one place,
  // the backend's LocationResolver, which refuses to guess.

  function renderWeatherUnavailable(message) {
    document.getElementById("weather-place").textContent = message;
    document.getElementById("weather-temp").innerHTML = "--&deg;";
    document.getElementById("weather-desc").textContent = "—";
    document.getElementById("weather-humidity").textContent = "--%";
    document.getElementById("weather-wind").textContent = "--";
  }

  async function loadWeather() {
    let res;
    try {
      res = await api("/v1/weather");
    } catch (_err) {
      renderWeatherUnavailable("Weather unavailable");
      return;
    }
    if (res.status === 409) {
      // No location configured. Say so plainly instead of inventing one.
      renderWeatherUnavailable("Set a location");
      return;
    }
    if (!res.ok) {
      renderWeatherUnavailable("Weather unavailable");
      return;
    }
    const data = await res.json();
    const temp = data.temperature;
    document.getElementById("weather-temp").innerHTML =
      temp === null || temp === undefined
        ? "--&deg;"
        : `${temp.toFixed(1)}&deg;${data.temperature_unit}`;
    // The provenance string says whether this is a device fix, a saved
    // default or an explicit request, so a wrong default is visible at a
    // glance rather than after acting on it.
    document.getElementById("weather-place").textContent = data.location.label;
    document.getElementById("weather-place").title = data.location.provenance;
    let desc = data.condition || "—";
    if (data.from_cache && data.cache_age_seconds > 60) {
      desc += ` (cached ${Math.round(data.cache_age_seconds / 60)}m ago)`;
    }
    if (data.location.is_stale) desc += " (location fix is over an hour old)";
    document.getElementById("weather-desc").textContent = desc;
    document.getElementById("weather-humidity").textContent =
      data.humidity_percent === null || data.humidity_percent === undefined
        ? "--%"
        : `${Math.round(data.humidity_percent)}%`;
    document.getElementById("weather-wind").textContent =
      data.wind_speed === null || data.wind_speed === undefined
        ? "--"
        : `${data.wind_speed.toFixed(1)} ${data.wind_unit}`;
  }

  function initWeather() {
    // Offer a device position only if the browser can provide one. A refusal
    // or a timeout is not an error state: the backend falls back to the
    // user's saved default, or asks for a city.
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        async (pos) => {
          try {
            await api("/v1/location/device-fix", {
              method: "POST",
              body: JSON.stringify({
                latitude: pos.coords.latitude,
                longitude: pos.coords.longitude,
                accuracy_metres: pos.coords.accuracy,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
              }),
            });
          } catch (_err) {
            // Backend unreachable; the saved default still applies.
          }
          loadWeather();
        },
        () => loadWeather(),
        { timeout: 5000 }
      );
    } else {
      loadWeather();
    }
    // Refresh periodically; the backend caches, so this is cheap.
    setInterval(loadWeather, 10 * 60 * 1000);
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
    // The one place the assistant's speaking state changes, so the one
    // place the self-transcription gate needs to be driven from. Both the
    // real-audio and browser-TTS paths route through here.
    if (isSpeaking) speechGate.beginPlayback();
    else speechGate.endPlayback(performance.now());
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
      const res = await api("/v2/capabilities");
      if (!res.ok) throw new Error("Session expired. Reopen the native app or pair again.");
      const capabilities = await res.json();
      capabilitySummary.textContent = capabilities.map(c => c.description).join(" ");
      statusEl.textContent = "Connected";
      await refreshConversations();
    } catch (_err) {
      statusEl.textContent = "Open the native app or pair this device";
    }
  }

  async function performAction(proposal, messageDiv) {
    const actionId = proposal.action_id;
    activeActionId = actionId;
    let settled = false;
    let expiryTimer = null;
    const row = document.createElement("div");
    async function resolve(accepted) {
      if (settled) return;
      settled = true;
      clearTimeout(expiryTimer);
      row.remove();
      clearPendingVoiceConfirmation();
      try {
        if (!accepted) {
          await api(`/v1/actions/${actionId}/cancel`, { method: "POST" });
          await speak("Cancelled.");
          return;
        }
        if (proposal.decision === "require_confirmation") {
          const approved = await api(`/v1/actions/${actionId}/approve`, {
            method: "POST", headers: { "X-Jarvis-Approval-Key": credentials.approval_key },
          });
          if (!approved.ok) throw new Error("Approval expired or unavailable. Ask again.");
        }
        const response = await api("/v1/tools/execute", {
          method: "POST", body: JSON.stringify({ action_id: actionId }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Action unavailable");
        addMessage(result.success ? "assistant" : "error", result.output);
        await speak(result.output);
      } catch (error) {
        addMessage("error", error.message);
      } finally {
        if (activeActionId === actionId) activeActionId = "";
      }
    }
    if (proposal.decision === "allow") return resolve(true);
    row.className = "confirm-row";
    const summary = document.createElement("span");
    summary.textContent = proposal.confirmation_summary || "Approve this action?";
    const yes = document.createElement("button");
    yes.className = "accept";
    yes.textContent = "Confirm";
    yes.onclick = () => resolve(true);
    const no = document.createElement("button");
    no.className = "reject";
    no.textContent = "Cancel";
    no.onclick = () => resolve(false);
    row.append(summary, yes, no);
    messageDiv.appendChild(row);
    setChatCollapsed(false);
    chatEl.scrollTop = chatEl.scrollHeight;
    await speak(summary.textContent + " Say confirm or cancel.");
    setPendingVoiceConfirmation(resolve);
    expiryTimer = setTimeout(() => resolve(false), 120000);
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
        content: call.content || "",
        arguments: call.arguments || {},
      }),
    });
    const proposal = await proposeRes.json();

    if (!proposeRes.ok) {
      addMessage("error", proposal.detail || "Action unavailable");
      return;
    }
    if (proposal.action_id) return performAction(proposal, messageDiv);

    if (proposal.decision === "deny") {
      if (spokenAnswer) await speak(spokenAnswer);
      addMessage("error", proposal.reason || "That action was denied.");
      return;
    }

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
      await api("/v1/system/terminate", { method: "POST",
        headers: { "X-Jarvis-Approval-Key": credentials.approval_key } });
    } catch (_err) {
      // The process is exiting either way — a fetch failure here (the
      // connection resetting mid-request as the server dies) isn't
      // something to surface.
    }
  }

  async function sendMessage(question) {
    const epoch = ++turnEpoch;
    if (activeActionId) {
      await api(`/v1/actions/${activeActionId}/cancel`, { method: "POST" });
      activeActionId = "";
      clearPendingVoiceConfirmation();
    }
    addMessage("user", question);
    conversationHistory.push({ role: "user", text: question });
    saveHistory();
    setThinking(true);
    try {
      const res = await api("/v2/turns", {
        method: "POST",
        body: JSON.stringify({ conversation_id: conversationId, turn_id: crypto.randomUUID(), question }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        addMessage("error", detail.detail || "The reasoning provider is unavailable.");
        setThinking(false);
        return;
      }
      const result = await res.json();
      if (epoch !== turnEpoch) {
        if (result.action_id) await api(`/v1/actions/${result.action_id}/cancel`, { method: "POST" });
        return;
      }
      conversationId = result.conversation_id;
      JarvisContract.validateTurn(result);
      const messageDiv = addMessage("assistant", result.spoken_answer);
      conversationHistory.push({ role: "assistant", text: result.spoken_answer });
      saveHistory();
      setThinking(false);
      if (result.action_id) {
        // handleToolCall decides what to actually speak — it may need to
        // say "Should I go ahead?" instead of this verbatim, depending on
        // whether confirmation turns out to be required (see there).
        await performAction(result, messageDiv);
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

  // ---- Microphone device picker -------------------------------------------
  //
  // Browsers withhold device *labels* until microphone permission has been
  // granted: before that, enumerateDevices() returns entries with empty
  // labels (and often a single blank-id entry). So this list cannot be
  // populated usefully on first load, and the hint says why instead of
  // showing an empty dropdown that looks broken.
  //
  // The saved choice is applied as `deviceId: {ideal}` by
  // JarvisVoice.audioConstraints -- never `{exact}`, which would make voice
  // fail permanently the first time that microphone is unplugged rather than
  // quietly falling back to the default.
  const micSelect = document.getElementById("mic-device");
  const micHint = document.getElementById("mic-hint");

  if (micSelect && micHint && navigator.mediaDevices) {
    const DEFAULT_LABEL = "System default";

    function describeSelection(deviceId, devices) {
      if (!deviceId) return "Using the system default device.";
      const match = devices.find((d) => d.deviceId === deviceId);
      if (match) return "Saved for next time.";
      // The saved device is not currently present. Say so plainly: the app
      // still works (it falls back to the default), but silently ignoring a
      // saved preference is the kind of thing that makes a microphone bug
      // impossible to diagnose.
      return "Saved device not connected — using the system default.";
    }

    async function refreshMicDevices() {
      let devices = [];
      try {
        devices = JarvisVoice.usableInputDevices(
          await navigator.mediaDevices.enumerateDevices()
        );
      } catch (_err) {
        micHint.textContent = "Could not list audio devices in this browser.";
        return;
      }

      const saved = JarvisVoice.loadSavedDeviceId(window.localStorage);

      if (!devices.length) {
        micSelect.innerHTML = "";
        const option = document.createElement("option");
        option.value = "";
        option.textContent = DEFAULT_LABEL;
        micSelect.appendChild(option);
        micHint.textContent =
          "Allow microphone access once to choose a specific device.";
        return;
      }

      micSelect.innerHTML = "";
      const fallback = document.createElement("option");
      fallback.value = "";
      fallback.textContent = DEFAULT_LABEL;
      micSelect.appendChild(fallback);

      devices.forEach((device) => {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.textContent = device.label;
        micSelect.appendChild(option);
      });

      // Keep the saved value selected even when that device is absent, so the
      // preference is not silently discarded by a repopulate.
      micSelect.value = devices.some((d) => d.deviceId === saved) ? saved : "";
      micHint.textContent = describeSelection(saved, devices);
    }

    micSelect.addEventListener("change", async () => {
      const chosen = micSelect.value;
      const stored = JarvisVoice.saveDeviceId(window.localStorage, chosen);
      let devices = [];
      try {
        devices = JarvisVoice.usableInputDevices(
          await navigator.mediaDevices.enumerateDevices()
        );
      } catch (_err) {
        devices = [];
      }
      if (!stored) {
        // Private mode or blocked site data: the choice applies to this
        // session but will not survive a reload. Better to say so than to
        // let the user believe it was remembered.
        micHint.textContent = "Applied, but this browser won't remember it.";
        return;
      }
      micHint.textContent = describeSelection(chosen, devices);
    });

    // Labels appear once permission is granted, and the list changes when a
    // headset is plugged in or removed.
    if (navigator.mediaDevices.addEventListener) {
      navigator.mediaDevices.addEventListener("devicechange", refreshMicDevices);
    }
    window.addEventListener("jarvis-mic-permission-granted", refreshMicDevices);

    refreshMicDevices();
  }
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
        // Prefer the classified, actionable message over the raw code:
        // "no-device" and "not-allowed" need completely different fixes.
        const known = JarvisVoice.MIC_ERRORS[event.error];
        addMessage("error", event.detail || (known && known.message) ||
          `Microphone error: ${event.error}`);
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
            cancelCurrentTurn();
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
        if (event.error === "no-device") {
          addMessage("error", JarvisVoice.MIC_ERRORS["no-device"].message +
            " Wake word mode needs a microphone.");
          return;
        }
        if (event.error === "device-busy") {
          addMessage("error", JarvisVoice.MIC_ERRORS["device-busy"].message);
          return;
        }
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

  // No public-page credentials. The host supplies a short-lived session,
  // or this page explicitly enrolls with a one-time code from that host.
  const connection = document.createElement("section");
  const capabilitySummary = document.createElement("p");
  capabilitySummary.textContent = "Connect to discover available capabilities.";
  connection.className = "card runtime-connection";
  connection.setAttribute("aria-label", "Device connection and conversations");
  const codeInput = document.createElement("input");
  codeInput.type = "password";
  codeInput.placeholder = "One-time pairing code";
  codeInput.autocomplete = "off";
  const pairButton = document.createElement("button");
  pairButton.textContent = "Pair this device";
  pairButton.onclick = async () => {
    const response = await fetch("/v2/devices/enroll", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: codeInput.value, name: "Browser" }) });
    codeInput.value = "";
    if (!response.ok) return addMessage("error", "Pairing failed or expired.");
    await window.jarvisBootstrap(await response.json());
  };
  const inviteButton = document.createElement("button");
  inviteButton.textContent = "Create phone pairing code";
  const pairingOutput = document.createElement("output");
  inviteButton.onclick = async () => {
    try {
      const response = await api("/v2/devices/invitations", { method: "POST",
        headers: { "X-Jarvis-Approval-Key": credentials.approval_key },
        body: JSON.stringify({ grants: ["pc_agent", "os_control"] }) });
      const data = await response.json();
      pairingOutput.textContent = response.ok ? data.code : (data.detail || "Pairing unavailable");
      setTimeout(() => { pairingOutput.textContent = ""; }, 120000);
    } catch (error) { addMessage("error", error.message); }
  };
  const conversationSelect = document.createElement("select");
  conversationSelect.setAttribute("aria-label", "Shared conversation");
  async function refreshConversations() {
    const response = await api("/v2/conversations");
    if (!response.ok) return;
    const data = await response.json();
    conversationSelect.replaceChildren(new Option("New conversation", ""));
    for (const c of data.conversations) conversationSelect.add(new Option(c.id, c.id));
    conversationSelect.value = conversationId;
  }
  conversationSelect.onchange = async () => {
    if (activeActionId) await api(`/v1/actions/${activeActionId}/cancel`, { method: "POST" });
    clearPendingVoiceConfirmation();
    activeActionId = "";
    conversationId = conversationSelect.value;
    chatEl.replaceChildren();
    if (!conversationId) return;
    const response = await api(`/v2/conversations/${conversationId}`);
    if (!response.ok) return;
    for (const turn of (await response.json()).turns) {
      addMessage("user", turn.question);
      if (turn.state === "completed") addMessage("assistant", JSON.parse(turn.response).spoken_answer);
    }
  };
  const refresh = document.createElement("button");
  refresh.textContent = "Refresh conversations";
  refresh.onclick = refreshConversations;
  const deviceList = document.createElement("div");
  const devicesButton = document.createElement("button");
  devicesButton.textContent = "Manage paired devices";
  devicesButton.onclick = async () => {
    const response = await api("/v2/devices");
    if (!response.ok) return;
    deviceList.replaceChildren();
    for (const d of (await response.json()).devices) {
      const button = document.createElement("button");
      button.textContent = `${d.name}: ${d.revoked ? "revoked" : "revoke"}`;
      button.disabled = Boolean(d.revoked);
      button.onclick = async () => {
        const result = await api(`/v2/devices/${d.id}`, { method: "DELETE",
          headers: { "X-Jarvis-Approval-Key": credentials.approval_key } });
        if (result.ok) { button.disabled = true; button.textContent = `${d.name}: revoked`; }
      };
      deviceList.appendChild(button);
    }
  };
  const logoutButton = document.createElement("button");
  const stopButton = document.createElement("button");
  stopButton.textContent = "Stop current action";
  stopButton.onclick = cancelCurrentTurn;
  logoutButton.textContent = "Disconnect session";
  logoutButton.onclick = async () => {
    await api("/v2/logout", { method: "POST" });
    credentials = null;
    clearPendingVoiceConfirmation();
    activeActionId = "";
    stopSpeaking();
    statusEl.textContent = "Disconnected";
  };
  connection.append(capabilitySummary, codeInput, pairButton, inviteButton, pairingOutput, conversationSelect,
    refresh, devicesButton, deviceList, stopButton, logoutButton);
  document.querySelector(".rail").prepend(connection);
})();
