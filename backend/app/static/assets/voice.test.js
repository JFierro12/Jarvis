/*
 * Tests for the voice path, run with `node --test`.
 *
 * Audio is synthesised, never recorded: a tone is speech, zeros are silence.
 * Nothing here needs a microphone, a browser or a person, which is the point
 * -- the failures being tested (hearing yourself, answering a cancelled turn,
 * approving an action with audio captured before it was proposed) are all
 * timing bugs that a human sitting at the machine would struggle to
 * reproduce on demand.
 *
 * What these tests do NOT cover is listed in docs/OVERNIGHT_LOG.md: real
 * device enumeration, real echo cancellation against real speakers, and the
 * Windows permission dialog all require physical verification.
 */
const test = require("node:test");
const assert = require("node:assert");

const V = require("./voice.js");

const SAMPLE_RATE = 48000;
const FRAME = 4096;
const FRAME_MS = (FRAME / SAMPLE_RATE) * 1000; // ~85.3ms

function silence(n = FRAME) {
  return new Float32Array(n);
}

function tone(n = FRAME, amplitude = 0.3) {
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = Math.sin((i / SAMPLE_RATE) * 2 * Math.PI * 220) * amplitude;
  return out;
}

function makeVad(overrides = {}) {
  return new V.VadStateMachine({
    sampleRate: SAMPLE_RATE,
    gate: overrides.gate,
    config: overrides.config,
  });
}

/* Feed frames and collect every event, advancing a synthetic clock. */
function feed(vad, frames, startAt = 0) {
  const events = [];
  let now = startAt;
  for (const frame of frames) {
    const event = vad.push(frame, now);
    if (event) events.push(Object.assign({ at: now }, event));
    now += FRAME_MS;
  }
  return { events, now };
}

function repeat(frame, count) {
  return Array.from({ length: count }, () => frame);
}

// ---------------------------------------------------------------------------
// Microphone error classification
// ---------------------------------------------------------------------------

test("a missing microphone is not reported as a permission problem", () => {
  const notFound = V.classifyMicError({ name: "NotFoundError" });
  assert.strictEqual(notFound.code, "no-device");
  assert.ok(!/denied|blocked/i.test(notFound.message),
    "telling someone with no microphone that access was denied sends them to a correctly-configured permission panel");
});

test("permission denial is reported as such, with the fix", () => {
  const denied = V.classifyMicError({ name: "NotAllowedError" });
  assert.strictEqual(denied.code, "not-allowed");
  assert.match(denied.message, /address bar/);
});

test("a microphone held by another application is distinguished", () => {
  assert.strictEqual(V.classifyMicError({ name: "NotReadableError" }).code, "device-busy");
});

test("an unplugged saved device reads as missing, not as a constraint bug", () => {
  assert.strictEqual(V.classifyMicError({ name: "OverconstrainedError" }).code, "no-device");
});

test("an unrecognised failure still produces a usable message", () => {
  const unknown = V.classifyMicError({ name: "WeirdError" });
  assert.strictEqual(unknown.code, "audio-capture");
  assert.ok(unknown.message.length > 0);
});

test("a null error does not throw", () => {
  assert.strictEqual(V.classifyMicError(null).code, "audio-capture");
});

// ---------------------------------------------------------------------------
// Device selection
// ---------------------------------------------------------------------------

function fakeStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

test("a saved microphone is a preference, not a hard requirement", () => {
  const constraints = V.audioConstraints("abc123");
  assert.deepStrictEqual(constraints.audio.deviceId, { ideal: "abc123" });
  assert.ok(!("exact" in constraints.audio.deviceId),
    "an exact deviceId makes voice fail permanently when that microphone is unplugged");
});

test("no saved device means no deviceId constraint at all", () => {
  assert.ok(!("deviceId" in V.audioConstraints("").audio));
});

test("echo cancellation stays on in every case", () => {
  for (const id of ["", "abc"]) {
    assert.strictEqual(V.audioConstraints(id).audio.echoCancellation, true);
  }
});

test("device preference round-trips through storage", () => {
  const storage = fakeStorage();
  assert.ok(V.saveDeviceId(storage, "mic-1"));
  assert.strictEqual(V.loadSavedDeviceId(storage), "mic-1");
  V.saveDeviceId(storage, "");
  assert.strictEqual(V.loadSavedDeviceId(storage), "");
});

test("blocked storage degrades to the default device rather than throwing", () => {
  const hostile = {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
    removeItem() { throw new Error("blocked"); },
  };
  assert.strictEqual(V.loadSavedDeviceId(hostile), "");
  assert.strictEqual(V.saveDeviceId(hostile, "x"), false);
  assert.strictEqual(V.loadSavedDeviceId(null), "");
});

test("devices without labels are filtered out of the picker", () => {
  // Before permission is granted the browser returns blank entries; a picker
  // full of empty rows is worse than saying permission is needed first.
  const devices = [
    { kind: "audioinput", deviceId: "", label: "" },
    { kind: "audioinput", deviceId: "a", label: "Headset" },
    { kind: "audiooutput", deviceId: "b", label: "Speakers" },
    { kind: "videoinput", deviceId: "c", label: "Webcam" },
  ];
  const usable = V.usableInputDevices(devices);
  assert.strictEqual(usable.length, 1);
  assert.strictEqual(usable[0].label, "Headset");
});

// ---------------------------------------------------------------------------
// Self-transcription: the headline bug
// ---------------------------------------------------------------------------

test("the assistant does not transcribe its own speech", () => {
  const gate = new V.SpeechGate({ tailMs: 400 });
  const vad = makeVad({ gate });

  gate.beginPlayback();
  // Loud "assistant voice" through the speakers for ~2 seconds.
  const { events } = feed(vad, repeat(tone(FRAME, 0.5), 24));

  assert.deepStrictEqual(events, [],
    "audio captured while the assistant is speaking must produce no utterance");
  assert.strictEqual(vad.speaking, false);
});

test("suppression continues after playback ends, covering speaker decay", () => {
  const gate = new V.SpeechGate({ tailMs: 400 });
  const vad = makeVad({ gate });

  gate.beginPlayback();
  gate.endPlayback(1000);

  // 200ms after playback ended: still inside the tail, still suppressed.
  assert.strictEqual(gate.isSuppressed(1200), true);
  assert.strictEqual(vad.push(tone(), 1200), null);

  // 500ms after: the tail has elapsed and the user can be heard again.
  assert.strictEqual(gate.isSuppressed(1500), false);
  assert.deepStrictEqual(vad.push(tone(), 1500), { type: "start" });
});

test("an utterance already in progress is abandoned when the assistant starts speaking", () => {
  const gate = new V.SpeechGate({ tailMs: 400 });
  const vad = makeVad({ gate });

  assert.deepStrictEqual(vad.push(tone(), 0), { type: "start" });
  vad.push(tone(), FRAME_MS);

  gate.beginPlayback();
  const event = vad.push(tone(), FRAME_MS * 2);
  assert.strictEqual(event.type, "discarded");
  assert.strictEqual(event.reason, "assistant-speaking");
  assert.strictEqual(vad.speaking, false);
});

test("playback does not poison the noise floor", () => {
  // If the assistant's own loud playback were averaged into the ambient
  // floor, the threshold would stay high for seconds afterwards and the
  // user's normal speaking voice would be ignored.
  const gate = new V.SpeechGate({ tailMs: 0 });
  const vad = makeVad({ gate });
  const floorBefore = vad.noiseFloor;

  gate.beginPlayback();
  feed(vad, repeat(tone(FRAME, 0.9), 40));
  gate.endPlayback(10000);

  assert.strictEqual(vad.noiseFloor, floorBefore,
    "the ambient floor must not be learned from the assistant's own audio");
});

test("barge-in is opt-in and honestly disables the protection", () => {
  const gate = new V.SpeechGate({ allowBargeIn: true });
  gate.beginPlayback();
  assert.strictEqual(gate.isSuppressed(0), false,
    "barge-in necessarily means the assistant can also hear itself");
});

test("a gate reset clears suppression", () => {
  const gate = new V.SpeechGate({ tailMs: 5000 });
  gate.beginPlayback();
  gate.endPlayback(0);
  assert.strictEqual(gate.isSuppressed(100), true);
  gate.reset();
  assert.strictEqual(gate.isSuppressed(100), false);
});

// ---------------------------------------------------------------------------
// Voice activity detection
// ---------------------------------------------------------------------------

test("speech followed by silence produces exactly one utterance", () => {
  const vad = makeVad();
  const frames = [...repeat(silence(), 3), ...repeat(tone(), 12), ...repeat(silence(), 12)];
  const { events } = feed(vad, frames);

  const utterances = events.filter((e) => e.type === "utterance");
  assert.strictEqual(utterances.length, 1);
  assert.strictEqual(events.filter((e) => e.type === "start").length, 1);
});

test("a brief blip is discarded rather than transcribed", () => {
  const vad = makeVad();
  // One frame of sound (~85ms) is well under the 300ms minimum.
  const { events } = feed(vad, [...repeat(silence(), 2), tone(), ...repeat(silence(), 12)]);

  const utterances = events.filter((e) => e.type === "utterance");
  const discarded = events.filter((e) => e.type === "discarded");
  assert.strictEqual(utterances.length, 0);
  assert.strictEqual(discarded.length, 1);
  assert.strictEqual(discarded[0].reason, "too-short");
});

test("pure silence never starts an utterance", () => {
  const { events } = feed(makeVad(), repeat(silence(), 50));
  assert.deepStrictEqual(events, []);
});

test("a very long utterance is cut at the maximum rather than growing forever", () => {
  const vad = makeVad({ config: { maxUtteranceMs: 1000 } });
  const { events } = feed(vad, repeat(tone(), 40));
  const utterances = events.filter((e) => e.type === "utterance");
  assert.ok(utterances.length >= 1);
  assert.strictEqual(utterances[0].reason, "max-length");
});

test("a short pause mid-sentence does not split the utterance", () => {
  const vad = makeVad();
  // ~170ms of silence, under the 700ms end-of-speech threshold.
  const frames = [
    ...repeat(tone(), 6), ...repeat(silence(), 2), ...repeat(tone(), 6),
    ...repeat(silence(), 12),
  ];
  const utterances = feed(vad, frames).events.filter((e) => e.type === "utterance");
  assert.strictEqual(utterances.length, 1, "a natural pause must not be treated as end of turn");
});

test("the threshold never drops below the configured floor", () => {
  const vad = makeVad();
  feed(vad, repeat(silence(), 100));
  assert.ok(vad.threshold() >= V.DEFAULT_VAD.minRms,
    "in a silent room the detector must not trigger on noise");
});

test("rms of silence is zero and of a tone is positive", () => {
  assert.strictEqual(V.rms(silence()), 0);
  assert.ok(V.rms(tone()) > 0.1);
  assert.strictEqual(V.rms(null), 0);
  assert.strictEqual(V.rms(new Float32Array(0)), 0);
});

// ---------------------------------------------------------------------------
// Stale turns and duplicates
// ---------------------------------------------------------------------------

test("a transcription that arrives after stop is dropped", () => {
  const turns = new V.TurnSequencer();
  const inFlight = turns.begin();
  turns.invalidate(); // user pressed stop
  assert.strictEqual(turns.accept(inFlight), false,
    "answering a turn the user cancelled posts a reply they did not ask for");
});

test("an older in-flight transcription loses to a newer one", () => {
  const turns = new V.TurnSequencer();
  const first = turns.begin();
  const second = turns.begin();
  assert.strictEqual(turns.accept(first), false);
  assert.strictEqual(turns.accept(second), true);
});

test("out-of-order completion does not resurrect the older turn", () => {
  const turns = new V.TurnSequencer();
  const first = turns.begin();
  const second = turns.begin();
  assert.strictEqual(turns.accept(second), true);
  assert.strictEqual(turns.accept(first), false);
});

test("the same turn cannot be delivered twice", () => {
  const turns = new V.TurnSequencer();
  const generation = turns.begin();
  assert.strictEqual(turns.accept(generation), true);
  assert.strictEqual(turns.accept(generation), false, "a retry must not post two conversation entries");
});

test("accepts() does not consume the generation", () => {
  const turns = new V.TurnSequencer();
  const generation = turns.begin();
  assert.strictEqual(turns.accepts(generation), true);
  assert.strictEqual(turns.accepts(generation), true);
  assert.strictEqual(turns.accept(generation), true);
});

// ---------------------------------------------------------------------------
// Spoken confirmation: the approval boundary
// ---------------------------------------------------------------------------

test("clear affirmatives approve", () => {
  for (const said of ["yes", "Yeah", "ok", "okay", "do it", "go ahead", "confirmed"]) {
    assert.strictEqual(V.interpretConfirmation(said), "approve", said);
  }
});

test("clear negatives reject", () => {
  for (const said of ["no", "nope", "cancel", "stop", "never mind", "abort"]) {
    assert.strictEqual(V.interpretConfirmation(said), "reject", said);
  }
});

test("a negative containing an affirmative phrase still rejects", () => {
  // "no, don't do it" contains "do it".
  assert.strictEqual(V.interpretConfirmation("no, don't do it"), "reject");
  assert.strictEqual(V.interpretConfirmation("cancel, go ahead and stop"), "reject");
});

test("ambiguous audio is unclear rather than guessed", () => {
  for (const said of ["", "uh", "what", "maybe", "the weather in denver", "yesterday"]) {
    assert.strictEqual(V.interpretConfirmation(said), "unclear", JSON.stringify(said));
  }
});

test("'yesterday' does not approve via a substring match on 'yes'", () => {
  assert.strictEqual(V.interpretConfirmation("yesterday was fine"), "unclear");
});

test("a confirmation needs an actual pending action", () => {
  assert.strictEqual(V.confirmationApplies(null, 100, 100).ok, false);
  assert.strictEqual(V.confirmationApplies({}, 100, 100).reason, "no-pending-action");
});

test("an expired action cannot be confirmed", () => {
  const pending = { actionId: "a1", proposedAt: 0, expiresAt: 1000 };
  assert.strictEqual(V.confirmationApplies(pending, 1500, 1500).reason, "expired");
});

test("audio captured before the action was proposed cannot approve it", () => {
  /*
   * The important one. Without this, speech recorded before the prompt --
   * including the assistant's own sentence describing the action -- could
   * retroactively authorise it.
   */
  const pending = { actionId: "a1", proposedAt: 1000, expiresAt: 5000 };
  const result = V.confirmationApplies(pending, 900, 1100);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, "spoken-before-proposal");
});

test("a confirmation spoken after the proposal and before expiry applies", () => {
  const pending = { actionId: "a1", proposedAt: 1000, expiresAt: 5000 };
  const result = V.confirmationApplies(pending, 1200, 1200);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.actionId, "a1");
});

test("confirmation classification does not itself authorise anything", () => {
  // interpretConfirmation returns a word, never an action id. Authorisation
  // requires confirmationApplies to match a specific pending action, and the
  // backend re-checks policy at execution regardless.
  const verdict = V.interpretConfirmation("yes");
  assert.strictEqual(typeof verdict, "string");
  assert.ok(!/action/i.test(verdict));
});
