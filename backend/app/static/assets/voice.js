/*
 * Voice-path logic, extracted so it can be tested.
 *
 * app.js is 1,300 lines of browser code with no tests, and the parts most
 * likely to be wrong are the stateful ones: deciding when speech starts and
 * stops, deciding whether a captured utterance is the assistant's own voice
 * coming back through the speakers, and deciding whether a transcription that
 * arrives late still belongs to the current turn. All three are pure state
 * machines over timestamps and amplitudes, so they do not need a microphone,
 * a browser or a human to test -- only synthetic frames.
 *
 * Loads as a plain <script> (assigns window.JarvisVoice) and as a CommonJS
 * module under Node, so the same source the browser runs is the source the
 * tests run. A separate "testable copy" would test the copy.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JarvisVoice = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // -----------------------------------------------------------------------
  // Microphone errors
  // -----------------------------------------------------------------------

  /*
   * "Microphone access was denied" shown to someone who simply has no
   * microphone plugged in sends them to a browser permission panel that is
   * already set correctly, where they will find nothing wrong and conclude
   * the app is broken. These are different problems with different fixes, so
   * they get different messages.
   */
  const MIC_ERRORS = {
    "not-allowed": {
      code: "not-allowed",
      message:
        "Microphone access is blocked. Click the icon in the address bar and allow the microphone, then try again.",
      recoverable: true,
    },
    "no-device": {
      code: "no-device",
      message:
        "No microphone was found. Connect one (or check Windows Sound settings) and try again.",
      recoverable: true,
    },
    "device-busy": {
      code: "device-busy",
      message:
        "The microphone is in use by another application. Close whatever is using it and try again.",
      recoverable: true,
    },
    "insecure-context": {
      code: "insecure-context",
      message:
        "Microphone capture needs a secure context. Open JARVIS over https or on localhost.",
      recoverable: false,
    },
    "audio-capture": {
      code: "audio-capture",
      message: "The microphone could not be started.",
      recoverable: true,
    },
  };

  function classifyMicError(err) {
    const name = (err && err.name) || "";
    switch (name) {
      case "NotAllowedError":
      case "SecurityError":
        return MIC_ERRORS["not-allowed"];
      case "NotFoundError":
      case "OverconstrainedError":
        // OverconstrainedError here means the saved deviceId no longer
        // exists -- a microphone that was unplugged. Same user-visible
        // problem as having none at all.
        return MIC_ERRORS["no-device"];
      case "NotReadableError":
      case "AbortError":
        return MIC_ERRORS["device-busy"];
      default:
        return MIC_ERRORS["audio-capture"];
    }
  }

  // -----------------------------------------------------------------------
  // Device selection and persistence
  // -----------------------------------------------------------------------

  const DEVICE_STORAGE_KEY = "jarvis.microphoneDeviceId";

  /*
   * Returns the constraint object for getUserMedia.
   *
   * The saved device is a *preference*, not a requirement: `deviceId: {ideal}`
   * rather than `{exact}`. With `exact`, unplugging the saved microphone makes
   * voice input fail permanently with OverconstrainedError until the user
   * finds the setting again. With `ideal` the browser falls back to the
   * default device and voice keeps working.
   */
  function audioConstraints(deviceId) {
    const audio = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    if (deviceId) audio.deviceId = { ideal: deviceId };
    return { audio: audio };
  }

  function loadSavedDeviceId(storage) {
    try {
      return (storage && storage.getItem(DEVICE_STORAGE_KEY)) || "";
    } catch (_err) {
      // Private mode, blocked site data: a missing preference is not a
      // failure, it just means "use the default device".
      return "";
    }
  }

  function saveDeviceId(storage, deviceId) {
    try {
      if (!storage) return false;
      if (deviceId) storage.setItem(DEVICE_STORAGE_KEY, deviceId);
      else storage.removeItem(DEVICE_STORAGE_KEY);
      return true;
    } catch (_err) {
      return false;
    }
  }

  /*
   * Before permission is granted, enumerateDevices() returns entries with
   * empty labels and empty deviceIds. Offering those as a picker shows a list
   * of blanks, so they are filtered out and the caller is told permission is
   * needed first.
   */
  function usableInputDevices(devices) {
    return (devices || []).filter(
      (d) => d && d.kind === "audioinput" && d.deviceId && d.label
    );
  }

  // -----------------------------------------------------------------------
  // Self-transcription suppression
  // -----------------------------------------------------------------------

  /*
   * Stops JARVIS hearing itself.
   *
   * With continuous/wake-word listening, the microphone is live while the
   * assistant's reply plays through the speakers. Without a gate it
   * transcribes its own voice, treats it as a new user turn, answers that,
   * and can loop. Browser echo cancellation helps for a laptop's built-in
   * pair but is unreliable with external speakers, and it is not something to
   * stake a conversation loop on.
   *
   * The tail matters as much as the playback window: speaker decay and room
   * reverb outlast the audio element's "ended" event, so capture stays
   * suppressed for `tailMs` afterwards.
   *
   * `allowBargeIn` turns the gate off deliberately. That is a real trade-off,
   * not a better setting: it permits interrupting the assistant mid-sentence
   * and simultaneously permits the assistant to hear itself. It is off by
   * default because a self-sustaining feedback loop is a worse failure than
   * not being able to interrupt.
   */
  function SpeechGate(options) {
    options = options || {};
    this.tailMs = options.tailMs === undefined ? 400 : options.tailMs;
    this.allowBargeIn = !!options.allowBargeIn;
    this._playing = false;
    this._endedAt = -Infinity;
  }

  SpeechGate.prototype.beginPlayback = function () {
    this._playing = true;
  };

  SpeechGate.prototype.endPlayback = function (now) {
    if (!this._playing) return;
    this._playing = false;
    this._endedAt = now;
  };

  SpeechGate.prototype.isSuppressed = function (now) {
    if (this.allowBargeIn) return false;
    if (this._playing) return true;
    return now - this._endedAt < this.tailMs;
  };

  SpeechGate.prototype.reset = function () {
    this._playing = false;
    this._endedAt = -Infinity;
  };

  // -----------------------------------------------------------------------
  // Turn sequencing
  // -----------------------------------------------------------------------

  /*
   * Drops transcriptions and replies that no longer belong to the current
   * turn.
   *
   * Transcription is asynchronous. If the user stops listening, or speaks
   * again before the previous request returns, the in-flight response is
   * stale: delivering it posts a turn the user did not just ask for, or
   * replays an answer after they cancelled. Requests can also complete out of
   * order, so "most recent response wins" is not sufficient -- acceptance is
   * by generation, and a generation is spent once accepted so a duplicate
   * delivery of the same turn cannot produce two conversation entries.
   */
  function TurnSequencer() {
    this._current = 0;
    this._accepted = -1;
  }

  TurnSequencer.prototype.begin = function () {
    this._current += 1;
    return this._current;
  };

  TurnSequencer.prototype.accepts = function (generation) {
    return generation === this._current && generation > this._accepted;
  };

  TurnSequencer.prototype.accept = function (generation) {
    if (!this.accepts(generation)) return false;
    this._accepted = generation;
    return true;
  };

  /* Invalidates everything in flight, e.g. on stop() or cancel. */
  TurnSequencer.prototype.invalidate = function () {
    this._current += 1;
    this._accepted = this._current;
  };

  // -----------------------------------------------------------------------
  // Voice activity detection
  // -----------------------------------------------------------------------

  const DEFAULT_VAD = {
    minRms: 0.015,
    floorMultiplier: 2.5,
    silenceMsToEnd: 700,
    minUtteranceMs: 300,
    maxUtteranceMs: 15000,
    prerollMs: 300,
  };

  function rms(frame) {
    if (!frame || !frame.length) return 0;
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    return Math.sqrt(sum / frame.length);
  }

  /*
   * The capture state machine, independent of Web Audio.
   *
   * `push(frame, now)` returns null, or an event describing what happened:
   *   {type:"start"}                     speech began
   *   {type:"utterance", durationMs}     an utterance completed and is worth sending
   *   {type:"discarded", reason}         captured audio deliberately thrown away
   *
   * Discards are reported rather than silently dropped so the UI can explain
   * why nothing happened, and so tests can assert on the reason.
   */
  function VadStateMachine(options) {
    options = options || {};
    this.config = Object.assign({}, DEFAULT_VAD, options.config || {});
    this.sampleRate = options.sampleRate || 48000;
    this.gate = options.gate || new SpeechGate({ allowBargeIn: true });
    this.noiseFloor = 0.01;
    this.speaking = false;
    this._utteranceSamples = 0;
    // Counted separately from _utteranceSamples, which includes the preroll.
    // The minimum-length filter exists to reject blips, and preroll is
    // audio from *before* speech was detected -- letting it count meant an
    // 85ms cough cleared a 300ms threshold on the strength of 300ms of
    // prepended silence, defeating the filter entirely.
    this._speechSamples = 0;
    this._silenceMs = 0;
    this._frames = [];
  }

  VadStateMachine.prototype._frameMs = function (frame) {
    return (frame.length / this.sampleRate) * 1000;
  };

  VadStateMachine.prototype.threshold = function () {
    return Math.max(this.noiseFloor * this.config.floorMultiplier, this.config.minRms);
  };

  VadStateMachine.prototype.push = function (frame, now) {
    const frameMs = this._frameMs(frame);

    // The assistant is speaking (or just stopped). Anything the microphone
    // hears now is presumed to be its own voice.
    if (this.gate.isSuppressed(now)) {
      if (this.speaking) {
        const abandoned = this._durationMs();
        this._resetUtterance();
        return { type: "discarded", reason: "assistant-speaking", durationMs: abandoned };
      }
      // Ambient tracking is also paused: letting the assistant's own
      // playback raise the noise floor would desensitise the detector to
      // the user's actual voice for seconds afterwards.
      return null;
    }

    const level = rms(frame);
    const threshold = this.threshold();

    if (!this.speaking) {
      this.noiseFloor = this.noiseFloor * 0.95 + level * 0.05;
      if (level > threshold) {
        this.speaking = true;
        this._silenceMs = 0;
        this._utteranceSamples = Math.ceil(
          (this.config.prerollMs / 1000) * this.sampleRate
        );
        this._utteranceSamples += frame.length;
        this._speechSamples = frame.length;
        return { type: "start" };
      }
      return null;
    }

    this._utteranceSamples += frame.length;
    if (level > threshold) this._speechSamples += frame.length;
    this._silenceMs = level > threshold ? 0 : this._silenceMs + frameMs;
    const durationMs = this._durationMs();
    const speechMs = (this._speechSamples / this.sampleRate) * 1000;

    if (this._silenceMs >= this.config.silenceMsToEnd) {
      this._resetUtterance();
      // Judged on how much of it was actually speech, not on total length.
      if (speechMs < this.config.minUtteranceMs) {
        return { type: "discarded", reason: "too-short", durationMs: durationMs,
                 speechMs: speechMs };
      }
      return { type: "utterance", durationMs: durationMs, speechMs: speechMs };
    }

    if (durationMs >= this.config.maxUtteranceMs) {
      this._resetUtterance();
      return { type: "utterance", durationMs: durationMs, reason: "max-length" };
    }
    return null;
  };

  VadStateMachine.prototype._durationMs = function () {
    return (this._utteranceSamples / this.sampleRate) * 1000;
  };

  VadStateMachine.prototype._resetUtterance = function () {
    this.speaking = false;
    this._utteranceSamples = 0;
    this._speechSamples = 0;
    this._silenceMs = 0;
    this._frames = [];
  };

  VadStateMachine.prototype.reset = function () {
    this._resetUtterance();
    this.noiseFloor = 0.01;
  };

  // -----------------------------------------------------------------------
  // Spoken confirmation
  // -----------------------------------------------------------------------

  const AFFIRMATIVE = [
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed",
    "do it", "go ahead", "approved", "approve", "affirmative", "please do",
  ];
  const NEGATIVE = [
    "no", "nope", "nah", "cancel", "stop", "don't", "dont", "negative",
    "never mind", "nevermind", "abort",
  ];

  function normalise(text) {
    return String(text || "").toLowerCase().replace(/[^a-z' ]+/g, " ").replace(/\s+/g, " ").trim();
  }

  /*
   * Interpret a spoken reply to a pending confirmation.
   *
   * Returns "approve", "reject" or "unclear". Anything not clearly one or the
   * other is "unclear" and must re-prompt: a confirmation is authorisation
   * for a real action, and guessing at ambiguous audio is the one place where
   * being helpful is actively dangerous. Negatives are checked first so
   * "no, don't do it" cannot match the "do it" in its own tail.
   *
   * This classifies words. It does not authorise anything: the caller must
   * still match the reply to a specific unexpired pending action, and the
   * backend policy engine re-checks every execution regardless.
   */
  function interpretConfirmation(text) {
    const said = normalise(text);
    if (!said) return "unclear";
    const words = said.split(" ");

    for (const phrase of NEGATIVE) {
      if (phrase.indexOf(" ") >= 0 ? said.indexOf(phrase) >= 0 : words.indexOf(phrase) >= 0) {
        return "reject";
      }
    }
    for (const phrase of AFFIRMATIVE) {
      if (phrase.indexOf(" ") >= 0 ? said.indexOf(phrase) >= 0 : words.indexOf(phrase) >= 0) {
        return "approve";
      }
    }
    return "unclear";
  }

  /*
   * Whether a spoken confirmation may be applied to a pending action.
   *
   * Three independent conditions, all required: an action must actually be
   * pending, it must not have expired, and the confirmation must have been
   * spoken *after* the action was proposed. The last one stops audio captured
   * before the prompt -- including the assistant's own description of the
   * action -- from retroactively approving it.
   */
  function confirmationApplies(pending, spokenAt, now) {
    if (!pending || !pending.actionId) return { ok: false, reason: "no-pending-action" };
    if (pending.expiresAt !== undefined && now >= pending.expiresAt) {
      return { ok: false, reason: "expired" };
    }
    if (pending.proposedAt !== undefined && spokenAt < pending.proposedAt) {
      return { ok: false, reason: "spoken-before-proposal" };
    }
    return { ok: true, actionId: pending.actionId };
  }

  return {
    MIC_ERRORS: MIC_ERRORS,
    classifyMicError: classifyMicError,
    DEVICE_STORAGE_KEY: DEVICE_STORAGE_KEY,
    audioConstraints: audioConstraints,
    loadSavedDeviceId: loadSavedDeviceId,
    saveDeviceId: saveDeviceId,
    usableInputDevices: usableInputDevices,
    SpeechGate: SpeechGate,
    TurnSequencer: TurnSequencer,
    VadStateMachine: VadStateMachine,
    DEFAULT_VAD: DEFAULT_VAD,
    rms: rms,
    interpretConfirmation: interpretConfirmation,
    confirmationApplies: confirmationApplies,
  };
});
