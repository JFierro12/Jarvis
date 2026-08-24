(() => {
  const TOKEN = document.querySelector('meta[name="jarvis-token"]').content;
  const authHeaders = { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" };

  function api(path, options = {}) {
    return fetch(path, { ...options, headers: { ...authHeaders, ...(options.headers || {}) } });
  }

  // ---------- System stats ----------
  function fmtBytes(gb) {
    return `${gb.toFixed(1)} GB`;
  }

  function formatUptime(seconds) {
    const s = Math.floor(seconds);
    const h = String(Math.floor(s / 3600)).padStart(2, "0");
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const sec = String(s % 60).padStart(2, "0");
    return `${h}:${m}:${sec}`;
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
      document.getElementById("uptime-value").textContent = formatUptime(stats.uptime_seconds);
    } catch (_err) {
      // Transient poll failure — next tick retries, nothing to surface.
    }
  }

  pollSystemStats();
  setInterval(pollSystemStats, 2000);

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
        `&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code`;
      const res = await fetch(url);
      const data = await res.json();
      const cur = data.current;
      document.getElementById("weather-temp").innerHTML = `${cur.temperature_2m.toFixed(1)}&deg;C`;
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

  function addMessage(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = text;
    chatEl.appendChild(div);
    chatEl.scrollTop = chatEl.scrollHeight;
    return div;
  }

  function setThinking(isThinking) {
    orbEl.classList.toggle("thinking", isThinking);
    if (isThinking) statusEl.textContent = "Thinking…";
    else if (!orbEl.classList.contains("speaking")) statusEl.textContent = "Online — local reasoning";
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
    statusEl.textContent = isSpeaking ? "Speaking…" : "Online — local reasoning";
    sendBtn.disabled = isSpeaking;
  }

  // Synthesizes `text` via /v1/speech/synthesize and pulses the orb once per
  // word, timed against the real audio duration (falls back to an
  // estimated per-word duration if synthesis fails or the audio has no
  // usable duration, e.g. the mock TTS provider's placeholder clip) — so
  // the animation stays word-synced regardless of which TTS provider
  // (mock/elevenlabs/future local) is configured.
  async function speak(text) {
    const words = text.split(/\s+/).filter(Boolean);
    if (words.length === 0) return;

    let audioEl = null;
    let audioUrl = null;
    try {
      const res = await api("/v1/speech/synthesize", { method: "POST", body: JSON.stringify({ text }) });
      if (res.ok) {
        const data = await res.json();
        const bytes = Uint8Array.from(atob(data.audio_base64), (c) => c.charCodeAt(0));
        const mime = data.format === "mp3" ? "audio/mpeg" : "audio/wav";
        audioUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
        audioEl = new Audio(audioUrl);
        await new Promise((resolve) => {
          audioEl.addEventListener("loadedmetadata", resolve, { once: true });
          audioEl.addEventListener("error", resolve, { once: true });
        });
      }
    } catch (_err) {
      audioEl = null;
    }

    const hasUsableDuration = audioEl && Number.isFinite(audioEl.duration) && audioEl.duration > 0;
    const fallbackMsPerChar = 55;
    const totalMs = hasUsableDuration
      ? audioEl.duration * 1000
      : words.reduce((sum, w) => sum + w.length * fallbackMsPerChar + 120, 0);

    const totalChars = words.reduce((sum, w) => sum + w.length, 0) || words.length;
    const wordDurationsMs = words.map((w) => Math.max(70, (w.length / totalChars) * totalMs));

    setSpeaking(true);
    if (audioEl) audioEl.play().catch(() => {});

    for (const wordMs of wordDurationsMs) {
      flashOrb();
      await sleep(wordMs);
    }

    setSpeaking(false);
    if (audioUrl) URL.revokeObjectURL(audioUrl);
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

  async function handleToolCall(call, requiresConfirmation, messageDiv) {
    const proposeRes = await api("/v1/tools/propose", {
      method: "POST",
      body: JSON.stringify({ tool_name: call.tool_name, target: call.target, arguments: call.arguments || {} }),
    });
    const proposal = await proposeRes.json();

    if (proposal.decision === "deny") {
      addMessage("error", proposal.reason || "That action was denied.");
      return;
    }

    const runExecute = async (confirmed) => {
      const execRes = await api("/v1/tools/execute", {
        method: "POST",
        body: JSON.stringify({
          tool_name: call.tool_name,
          target: call.target,
          arguments: call.arguments || {},
          confirmed,
        }),
      });
      const result = await execRes.json();
      addMessage(result.success ? "assistant" : "error", result.output || (execRes.ok ? "Done." : "Failed."));
    };

    if (proposal.decision === "allow" && !requiresConfirmation) {
      await runExecute(false);
      return;
    }

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
    accept.onclick = async () => {
      row.remove();
      await runExecute(true);
    };
    reject.onclick = () => row.remove();
    row.appendChild(accept);
    row.appendChild(reject);
    messageDiv.appendChild(row);
  }

  async function sendMessage(question) {
    addMessage("user", question);
    setThinking(true);
    try {
      const res = await api("/v1/reason", {
        method: "POST",
        body: JSON.stringify({ context: [], question, available_tools: [] }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        addMessage("error", detail.detail || "The reasoning provider is unavailable.");
        setThinking(false);
        return;
      }
      const result = await res.json();
      const messageDiv = addMessage("assistant", result.spoken_answer);
      setThinking(false);
      await speak(result.spoken_answer);
      if (result.proposed_tool_call) {
        await handleToolCall(result.proposed_tool_call, result.requires_confirmation, messageDiv);
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
    sendMessage(text);
  });

  ensureSession();
})();
