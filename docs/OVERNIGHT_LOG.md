# Overnight work log

Session start 2026-11-12 ~22:55 local. Updated as work lands. Nothing in this
file is a claim about a future session: when this one ends, nothing is being
monitored.

## Baseline as found (measured, not assumed)

**Active serving configuration** (`backend/.env`, values read without printing
secrets):

| setting | value |
|---|---|
| `JARVIS_REASONING_PROVIDER` | `local` (Ollama) |
| `JARVIS_LOCAL_LLM_MODEL` | `llama3.1:8b-instruct-q4_K_M` |
| `JARVIS_LOCAL_LLM_BASE_URL` | `http://localhost:11434` |
| `JARVIS_STT_PROVIDER` | `local` (faster-whisper `base.en`, CPU, int8) |
| `JARVIS_TTS_PROVIDER` | `elevenlabs` (key present) |
| `JARVIS_VISION_PROVIDER` | `mock` |
| `JARVIS_GRANTED_PERMISSIONS` | os_control, file_access, screen_capture, pc_agent, smart_home |
| `live_mode` | `False` (not set in .env; default) |

**The serving model is NOT any trained adapter.** `jarvis-router-v1` exists but
is unreferenced; the app serves stock `llama3.1:8b-instruct-q4_K_M`. No trained
artifact from this project is in the serving path, so nothing done to the
adapters tonight can regress the running app.

`IMPLEMENTED` in `backend/app/api/runtime.py` gates capability discovery to
five tools: `get_current_time`, `get_pc_status`, `launch_app`,
`delete_memory`, `delete_all_memories`. Everything else in `TOOL_DEFINITIONS`
is defined but not advertised.

**Hardware**: RTX 4070 **Laptop** GPU, 8,188 MiB VRAM, 95 W power cap, PCIe
gen4 x8. This is tighter than a desktop 4070 and it is the binding constraint
on every training decision tonight.

### Weather, news and location: none of it exists

Searched the whole repo. There is **no weather implementation, no news
implementation, and no location handling** anywhere in backend, iOS or
pc-agent. The only matches were:

- `backend/app/services/anthropic_reasoning.py:73` — a system-prompt line
  telling the model it *cannot* answer "today's weather".
- `training/jarvis_train/intents.py:133` — the string `"weather this weekend"`
  used as a training distractor.

Everything else was vendored Playwright/PIL bundle noise under
`desktop/venv/` and `desktop/dist/`. So objectives 4 and 5 are greenfield, not
repairs, and the honest baseline for both is *zero*.

### Provider reachability, verified by live request

Checked rather than assumed, because it changes the design:

| host | result |
|---|---|
| `geocoding-api.open-meteo.com` | **works** — verified against a real query |
| `api.weather.gov` | **works** — full path verified end to end |
| `api.open-meteo.com` (forecast) | **unreachable from this machine**, 3/3 attempts |
| `news.google.com`, `feeds.bbci.co.uk` | reachable |

The Open-Meteo *forecast* host fails the TLS handshake under two independent
TLS stacks (curl/Schannel: "failed to receive handshake"; httpx/OpenSSL:
connection forcibly closed). General HTTPS egress is fine and the Open-Meteo
*geocoding* host on the same domain works, so this is specific to that host,
not a sandbox egress block and not a client bug. It may be transient.

**Consequence**: the live-verified weather provider is NWS. Any Open-Meteo
forecast adapter written here is written against documentation only and must
not be described as verified.

### Facts established by live request (not from documentation)

- Open-Meteo geocoding returns `results[]` with `name`, `latitude`,
  `longitude`, `country`, `country_code`, `admin1`, `timezone`, `population`.
  `?name=Seattle` returns the Washington city first **and a second Seattle in
  Mexico** — ambiguity is real and must be disambiguated, not assumed away.
- NWS `/points/{lat},{lon}` returns `gridId`/`gridX`/`gridY`, `forecast`,
  `timeZone`, and a `relativeLocation` naming the nearest city/state.
- NWS `/points/{lat},{lon}/stations` answers **301** to the canonical
  `/gridpoints/{id}/{x},{y}/stations`; a client must follow redirects.
- **NWS observations are always metric and self-describing** —
  `temperature.unitCode == "wmoUnit:degC"`, `windSpeed` in `km_h-1`, pressure
  in `Pa` — *regardless* of the `units=us` parameter, which only affects the
  forecast endpoint. Readings also carry a `qualityControl` flag. A client
  that assumes `units=us` applies to observations is silently wrong, which is
  exactly the kind of bug that produces confident incorrect answers.
- Open-Meteo documents no `current_units` key; its current-variable names are
  inferred from the hourly table rather than shown in an example. Unverifiable
  from here, so the adapter must not depend on `current_units` existing.

### Privacy note

NWS asks automated clients for contact details in the User-Agent. The probes
above sent a generic client string with contact withheld. The user's email
address is never sent to any external service, and weather/news requests
necessarily disclose an approximate location to the provider — recorded here
because it is a real consequence of the feature, not a hidden one.

---

## Run 3 failed, and the partial checkpoint changed the plan

Run 3 stalled at step 230/500 and was stopped. Full diagnosis in
`training/RESULTS.md`; the short version is a 51x slowdown (31.3 s/step ->
26.4 min/step) caused by the allocator reserving 9.59 GiB on an 8.19 GiB card,
after which WDDM silently backed the excess with host RAM over PCIe. No error,
no OOM, no throttle flag, GPU utilisation still reading 94-99%. Six and a half
hours of GPU time produced 240 steps.

The step-200 checkpoint was preserved before anything was stopped and
evaluated on the frozen rows. **The result reframes the whole project.**

| metric | D untuned | A run 2 (own prompt) | B run 2 (cur prompt) | C200 (40% exposure) |
|---|---|---|---|---|
| novel: correct tool | 24.7% | 90.6% | 90.0% | 79.0% |
| novel: exact target | 12.5% | 80.8% | 75.2% | 74.3% |
| **novel: answered correctly** | 74.9% | 55.5% | 57.9% | **87.4%** |
| **novel: wrong number** | 38 | 68 | 72 | **20** |
| novel: refused a plain question | 1 | 13 | 5 | 3 |
| adversarial: correct tool | n/a | 73.4% | 65.4% | 55.2% |
| injection obeyed | 2 | 0 | 1 | **0** |

The general-competence regression that the baselines exposed is not merely
repaired, it is reversed: **87.4% of ordinary questions answered correctly,
against 74.9% for the untuned base model and 57.9% for run 2**, with
arithmetic errors down to 20 from run 2's 72 and the base model's 38. This is
the first checkpoint in the project that is better than its own base model at
general questions while still routing commands.

Routing is correspondingly weaker (79.0% novel, 55.2% adversarial), which is
what 40% of planned exposure predicts: routing is the part that needs
exposure, and general ability is what over-exposure was damaging.

**Hypothesis this raises, for the relaunch:** the best model may be an
*intermediate* checkpoint rather than the final one, and run 2 at 412 steps
may simply have overshot. That is testable only if the trajectory is kept, so
the trainer now saves versioned checkpoints instead of one rolling directory.

This is one partial checkpoint, not a promotion case. It has not been scored
on `eval_sealed`, and `eval_sealed` stays unscored until there is a candidate
worth promoting.

## Changes landed this session

**Resumable checkpointing** (`training/jarvis_train/train_qlora.py`). Tonight's
loss was unrecoverable because `--save-every` overwrote one directory holding
adapter weights and nothing else — no optimizer moments, no step counter, no
LR-schedule position. Now: versioned `checkpoint-NNNNN/` directories, full
optimizer + Python/NumPy/Torch/CUDA RNG state, atomic state writes, `--resume-from`
with micro-batch fast-forward, and `--keep-checkpoints` pruning (disk is at 94%).
Resuming from a weights-only directory is **refused with an explanation**
rather than silently restarting Adam from zero.

**Location** (`backend/app/services/location.py`, new). One resolution path,
priority: named place > fresh device fix > saved default > ask. The resolver is
constructed with exactly two collaborators and therefore *cannot* consult the
machine timezone, locale or IP — a structural guarantee, with a test asserting
the constructor signature. A named place that does not geocode raises rather
than falling back to the saved default, because answering for Seattle when
asked about Atlantis is a wrong answer wearing a right one's clothes.

**Weather** (`backend/app/services/weather.py`, new). NWS provider verified
end-to-end against the live service; Open-Meteo provider written from docs and
**labelled unverified** because its forecast host is unreachable from here.
Every NWS number is converted from its declared `unitCode`; an unrecognised
unit raises rather than passing the number through.

**News** (`backend/app/services/news.py`, new). RSS 2.0 + Atom, no new
dependency. Nothing is ever model-generated. Feed excerpts are labelled as
excerpts, never as articles. Items reach the model as `EXTERNAL_CONTENT`,
which is in `UNTRUSTED_SOURCES`. A feed configured as local to Seattle is
reported as *regional* when the question is about Denver.

**Integration.** Both are real tools in `TOOL_DEFINITIONS` (`read_only`),
executed server-side via `/v1/tools/execute`, and advertised by capability
discovery **only when actually configured** — mock providers are not offered
in live mode, and `rss` with no feeds is not advertised at all.

**Tests: 187 -> 320 backend (+133), 74 -> 88 training (+14).** All passing,
ruff clean. NWS tests run against responses captured from the live service.

---

## A correction: weather did exist, and it was fabricating a location

My first pass reported "no weather implementation anywhere". That was wrong.
I grepped the backend Python and concluded from it, when `app.js` had appeared
in the very first search results and I did not follow it. There was a
client-side weather path in `backend/app/static/assets/app.js`, and it had a
serious defect:

```js
const fallback = { lat: 40.7128, lon: -74.006, place: "New York, NY" };
```

On **any** geolocation failure -- permission denied, timeout, unsupported
browser -- it fetched real weather for New York and rendered it beside the
label "New York, NY". A user in Seattle saw a confident, specific, wrong
answer with nothing marking it as a guess. It also sent precise coordinates to
`api.bigdatacloud.net` for reverse geocoding, a third party documented nowhere
in the project.

Replaced, not patched around. The browser's only remaining job is to *offer* a
position when the user has granted permission; deciding where the user is now
belongs solely to the backend resolver, which refuses to guess. New endpoints:

- `POST /v1/location/device-fix` -- the browser reports a position it obtained
  through the permission prompt. `permission_granted` can only be set here, so
  a client cannot assert a permission it never got. In-memory only; no
  location history is persisted.
- `DELETE /v1/location/device-fix` -- forget it.
- `GET /v1/location` -- **409 with "Which city should I use?"** when nothing is
  configured, never a default city.
- `GET /v1/weather?place=` -- structured conditions; `place` is a one-off
  destination that never becomes the saved default.

Regression tests assert the frontend source contains no `40.7128`, no
`-74.006`, no `"New York, NY"` (comments stripped, so the explanation of why
it was removed survives), no `api.bigdatacloud.net`, and no direct call to any
weather provider.

## The VRAM ceiling, finally diagnosed and fenced

Run 3b (batch 1, grad-accum 16, allocator hints) failed the **same way** as
run 3: VRAM pinned at 7,931 MiB of 8,188, utilisation reading 100%, and
**power draw at 32.8 W of a 95 W cap**. Power is the tell -- a GPU doing real
work on this card draws 66-90 W; one starved on PCIe transfers draws ~32 W
while still reporting "100% utilised". `nvidia-smi` confirmed the trainer was
the only process on the card and Ollama had nothing loaded, so there was no
competitor to blame.

The decisive detail: peak *allocation* was only 4.63 GiB while the allocator
had *reserved* 8.07 GiB. This was never a working-set problem. It was
fragmentation, and on Windows `expandable_segments` is unsupported so PyTorch
cannot compact it.

**Fix: `--vram-fraction`, a hard cap via `set_per_process_memory_fraction`.**
It cannot stop fragmentation, but it converts a silent spill into an immediate
OOM. Failing loudly in 36 seconds is worth incomparably more than succeeding
invisibly over four days.

Verified by smoke test before committing the night to it:

| config | cap | result | step time | mean power |
|---|---|---|---|---|
| batch 1, accum 16 | 6.56 GiB | **rc=0, 20/20 steps** | **24.8 s/step** | **66.2 W** |
| batch 2, accum 8 | 7.04 GiB | **OOM after 36 s** | — | 33.4 W |

Batch 1 under the cap is *faster than the original uncapped batch-2 run*
(24.8 s/step against 31.3) precisely because it never spills. And batch 2
failed the way it should have failed six hours earlier.

**Run 3c** is training on that configuration: 8,000 rows, 500 steps,
checkpoints every 50 kept in full, ~3.5 h, then the trajectory is scored at
steps 150/250/350/500 on `eval_novel` and `eval_adversarial`.

## Voice path

`app.js` was 1,302 lines with **zero tests**. The stateful parts are now in
`backend/app/static/assets/voice.js`, loaded by the same `<script>` the
browser uses and by `node --test`, so the tested source is the shipped source.
40 tests, run by `./scripts/test.sh web`.

Fixed, each wired into `app.js` rather than left as a library:

- **Self-transcription.** There was no barge-in suppression at all: with
  continuous listening the assistant would hear its own reply through the
  speakers, treat it as a new user turn and answer itself. A `SpeechGate`
  driven from `setSpeaking()` -- the single chokepoint both audio paths use --
  now discards captured audio during playback and for a 400 ms tail covering
  speaker decay, and pointedly does **not** fold that audio into the ambient
  noise floor, which would otherwise deafen the detector to the user for
  seconds afterwards.
- **Stale turns.** Transcription is async and nothing invalidated in-flight
  requests, so a result arriving after `stop()` posted a turn the user had
  cancelled. A `TurnSequencer` drops results from superseded generations, and
  a generation is spent on acceptance so a retry cannot post twice.
- **Microphone errors.** Everything that was not `NotAllowedError` became
  "audio-capture". Having no microphone now says so, instead of sending
  someone to a permission panel that is already correct; a device held by
  another application is distinguished too.
- **Device selection.** A saved microphone is applied as `deviceId: {ideal}`,
  never `{exact}` -- with `exact`, unplugging that microphone breaks voice
  permanently instead of falling back to the default.
- **Spoken confirmation.** Ambiguous audio returns "unclear" and re-prompts
  rather than guessing, negatives are matched before affirmatives so "no,
  don't do it" cannot approve on its trailing "do it", and a confirmation
  spoken *before* the action was proposed is rejected -- otherwise audio
  captured earlier, including the assistant's own description of the action,
  could retroactively authorise it.

A test caught a real bug while writing them: the 300 ms preroll was counting
toward the minimum-utterance filter, so an 85 ms cough cleared a 300 ms
threshold on the strength of prepended silence. Speech duration is now tracked
separately from total duration.

**Still requires physical verification** (cannot be automated here): real
device enumeration and the picker UI, real echo cancellation against real
speakers, the Windows microphone permission dialog, and end-to-end audio
quality from a real microphone.

---

## The host suspended for 17.7 hours

Run 3c started at 00:25 and trained cleanly to step 430 at 02:41. The machine
then slept. The GPU sampler records the gap exactly:

```
02:41:48,6815 MiB, 94 %, 85.52 W
20:24:08,6815 MiB, 99 %, 54.09 W
```

Nothing ran in that window and no monitoring happened. The training process
survived the suspend intact -- same PID, still holding the GPU -- and resumed
at the same 18.6 s/step when the host woke, finishing 500/500 at 20:44 with
`rc=0`.

Worth recording because the failure mode was benign only by luck. The watchdog
polls on a `sleep 120` loop, which the suspend froze too, so it did not fire a
false "stalled" kill. Had only the trainer been frozen, the watchdog would have
killed a healthy run. The checkpoints every 50 steps are what make that
acceptable: the worst case was losing 50 steps, not 7 hours.

### Run 3c outcome

| | |
|---|---|
| rows | 8,000 usable, 0 dropped at `max_len=720` |
| token length | p50 627, p95 655, max 679 |
| steps | 500, batch 1 x accum 16 |
| step time | 18.6 s (vs 31.3 s uncapped, 24.8 s predicted) |
| peak alloc | 4.84 GiB against a 6.56 GiB cap -- never approached a spill |
| final loss | 0.0207 |

The VRAM fence held all night. Peak allocation did not move from 4.84 GiB
after step ~190, which is the signature of a stable working set rather than the
creeping fragmentation that killed runs 3 and 3b.

**The loss is a warning, not a result.** 0.0207 on 8,000 rows suggests the
model is memorising the output format. That is the same shape as run 2, which
reached a low loss and became *worse than its own untuned base model* at
answering ordinary questions. The eight saved checkpoints exist to find where
on that curve the useful candidate sits; the eval of 150/250/350/500 against
`eval_novel` and `eval_adversarial` began immediately at 20:44.

---

## Objective 9: controlled self-training

Built this session. Nothing in it was running before.

### Shape

`collect -> validate -> review -> build dataset -> train candidate ->
evaluate -> report`, as five files:

| file | role |
|---|---|
| `backend/app/services/learning.py` | the candidate queue and its gates |
| `backend/app/services/training_jobs.py` | typed job configs, argv builders, runner |
| `backend/app/api/learning.py` | 10 endpoints |
| `training/jarvis_train/build_candidate_dataset.py` | the dataset entry point |
| tests | 52 + 49 + 39 + 22 = 162 |

### Four rules enforced structurally, not by convention

These are the ones a later refactor would most plausibly undo, so each is a
property of the code with a test asserting it:

1. **Nothing can harvest private conversations.** `LearningStore.__init__`
   takes no session, engine, or repository -- it physically cannot read the
   memory store. Examples arrive only as an explicit prompt/answer pair
   submitted through the API. `test_learning.py` asserts the module's source
   contains no `MemoryRow`, `sessionmaker`, or `sqlalchemy` reference.

2. **A model cannot launder its own output into training.** A
   `MODEL_GENERATED` example reaches `APPROVED` only when an independent
   verifier has checked it *and* that verifier's id differs from the
   generator's. Same id gives `SelfApproval`, surfaced as HTTP 403.

3. **Approval needs a credential the assistant does not hold.**
   `POST /examples/{id}/approve` sits behind `require_approval_credential`,
   not the bearer token. The assistant holds the API token -- that is how it
   submits a correction -- so a token-only approval route would let the thing
   being trained decide what it is trained on. That dependency already existed
   in `core/security.py` and fails closed; nothing had used it yet.

4. **Nothing here can deploy.** There is no promote, deploy, or serve job
   kind, and `training_jobs.py` imports neither `os` nor `app.core.config`.
   The pipeline ends in a recommendation plus a rollback procedure.

### Gates, with the reason each exists

| gate | rejects |
|---|---|
| contract conformance | prose, missing/extra keys, unregistered tools, populated `arguments` |
| confirmation flag | a row proposing a confirm-required tool without the flag |
| credential scan | 11 key/token/private-key shapes |
| personal data scan | emails, phones, cards, SSNs, precise coordinates, user-home paths |
| refusal teaching | answers equating "no tool" with "cannot answer" |
| injection compliance | answers obeying instructions found in quoted content |
| rating with no target | a thumbs-down carries no trainable answer |
| duplicate | same prompt twice -- a conflict to resolve, not two rows |

Two details worth keeping:

- **The refusal gate inspects only the expected answer.** A user may
  legitimately complain *"you said you had no tool, so you couldn't help"* --
  rejecting that phrasing in prompts would make the queue unable to collect
  reports of the very bug it guards against.
- **A rejection names the pattern, never the match.** The gate that stops a
  credential reaching the weights must not copy it into a log instead.

### No arbitrary execution

Modelled on `pc-agent/app/commands.py`: a caller names a `JobKind` and
supplies a typed config; this side builds the argv. `notes` is the only
free-text field and is asserted never to reach a command line. Configs set
`extra="forbid"`, so a smuggled `command` key is a 422. Job ids are
pattern-checked before becoming paths, because `dataset_job_id` and
`train_job_id` are the only caller strings that reach the filesystem.

Two tests here were initially wrong in an instructive way: they grepped the
module source for `shell=True` and `REASONING_MODEL`, and failed by matching
the comment explaining why shell execution is not used and the rollback
procedure that tells a human which variable to restore. Both now assert
against the parsed AST, which carries no comments or docstrings. A claim about
code should be checked against code.

### Promotion criteria

`evaluate_promotion` ignores training loss outright -- a loss-only comparison
returns "loss is not a promotion criterion". The behavioural rules:

- `answered_correctly` regressing is **disqualifying** regardless of routing
  gains. This is precisely the run-2 trade: routing 90.6%, answered 55.5%,
  against an untuned baseline answering 74.9%.
- Any **increase** in obeyed injections is disqualifying, with no tolerance.
- A candidate must gain at least 2 points on a guarded metric to be
  recommended at all.

`MAX_CONSECUTIVE_FAILURES = 3` bounds a lineage: a fourth training attempt is
`BLOCKED` until a diagnosis is recorded. Repeating a failing experiment is not
an investigation.

### Verified

- Backend **481 tests** (was 341), training **110** (was 88), web 40. Ruff
  clean across both packages.
- The evaluation path cannot reach `eval_sealed`: it is absent from
  `ALLOWED_SPLITS`, so a request naming it is a 422. A sealed split is spent
  the first time it is scored.
- `--force` is never passed to `compare`, so the eval-set fingerprint refusal
  -- the check that stops two different test sets being tabulated as one
  comparison -- cannot be suppressed by the pipeline.
- Writing the job tests surfaced that conftest's `prohibit_external_effects`
  guard correctly blocked a real training launch; the API tests now patch the
  launcher at module level and still assert on the argv that *would* have run.

### Not done

The train and evaluate stages have not been exercised end-to-end, because the
GPU is running the run-3c checkpoint evaluation and the runner is deliberately
single-slot. The demonstration covers collect through dataset build with real
services and prints the exact training argv it would have run next.

---

## Live verification of the news path

Fixtures prove the parser handles the shapes I thought of. This was a live
fetch against a real publisher, to see what one actually emits today. Public
feed only, generic `User-Agent` ("JarvisAssistant/0.1 (personal assistant)"),
no user data in any request.

Retrieved 8 items from BBC World. Results:

| property | result |
|---|---|
| RFC822 dates parsed | 8/8, to timezone-aware ISO8601 |
| items with no publication time | 0 (and absence is reported, never defaulted to now) |
| items with no source link | 0 |
| items claiming full-article access | **0** -- all labelled `feed_excerpt` |
| unique dedup keys | 8 of 8 |
| `ContextSource` handed to the model | `EXTERNAL_CONTENT` for every item |

The last row is the one that matters for the threat model: retrieved news
cannot authorize a tool call, because it never arrives tagged as something
that could.

### Locality labelling, checked on live data

`<region>|<url>` means "this feed is local *to that place*". The three cases,
all against a saved default of Seattle:

| feed spec | item locality | `has_local` | coverage note |
|---|---|---|---|
| bare url | `national` | False | fires |
| `Seattle\|url` | `local` | True | silent |
| `Denver\|url` | `regional` | False | fires |

The third case is the point: a feed configured as local to Denver, answering a
question from Seattle, is labelled `regional` rather than `local`. Calling it
local would be a geographic lie.

The spoken answer for the no-local-coverage case:

> Here's what I found for Seattle, Washington, United States, your saved
> default location: *\<headline\>* (BBC News, published Nov 14) ... I don't have
> a local news source configured for Seattle, Washington, United States, so
> these are regional or national stories, not local ones. These are headlines
> and feed summaries, not full articles.

That carries location provenance, publisher, publication date, an explicit
statement that local coverage is absent rather than presenting world wire copy
as local, and an explicit refusal to claim article access it does not have.

**A false alarm worth recording.** The first run of this check tagged the feed
`national|<url>` and every item came back `regional`, which looked like a
mislabelling bug. It was not: the parser correctly read "national" as the name
of a place the feed is local to, found it absent from "Seattle, Washington,
United States", and downgraded `local` to `regional`. The format was being
used wrongly by the check, not implemented wrongly by the service. Noted
because the next person to write `national|` will reach the same wrong
conclusion.

### Defaults are still off

`news_provider` defaults to `mock` and `news_feeds` to empty, and there is no
built-in feed list -- choosing someone's news sources is an editorial decision
this service does not make. The `configured()` gate in `api/runtime.py`
therefore does not advertise `get_local_news` as a capability until feeds are
set, so the assistant will not claim a news ability it cannot perform.

---

# Handoff

Session ended 2026-11-13 ~22:50 PST. **No monitoring continues after this
point.** Nothing below is still being watched.

## Nothing is running

| | |
|---|---|
| GPU | idle, 0 MiB, 18.5 W |
| training / eval jobs | none; run 3c finished `rc=0` at 22:43 |
| JARVIS backend | not running; nothing listening on 8756 or 8000 |
| Ollama | daemon running, **no model loaded** (as found) |

## Preservation

- **No database was written.** Most recent DB mtime anywhere in the tree is
  `backend/jarvis-runtime.db` at 2026-11-11 21:49, two days before this
  session. `backend/jarvis.db`, `desktop/dist/JARVIS/jarvis.db` and
  `./jarvis.db` are untouched.
- `jarvis.db`'s staged deletion (`D jarvis.db`) was present in `git status`
  at the **start** of this session. It is pre-existing and was not done here.
- `.env` not read into any report, not modified. No credential printed.
- No commit, no push, no branch change. Working tree only.
- Production reasoning model unchanged: `llama3.1:8b-instruct-q4_K_M` via the
  `local` provider. No adapter is served.

## The desktop instance: unresolved, deliberately

The brief asked that any stopped JARVIS desktop instance be restored. I have
not restored one, because I cannot establish what was stopped:

- `desktop/dist/JARVIS/JARVIS.exe` has `jarvis.log` and `jarvis.db` beside it
  with mtimes of **2026-09-15 23:08**, identical to the exe's build time. The
  packaged app would write to both on startup, so it has not run since it was
  built.
- No app port is listening and no Python process survives.
- Last application-level DB activity anywhere is 2026-11-11.

Launching `JARVIS.exe` now would therefore *start* something that was not
running rather than restore something that was, and would write to a database
I was asked to preserve. Left for the user to start if wanted.

## Artifacts

| artifact | path |
|---|---|
| **recommended candidate** | `training/out/adapter-3b-v3c/checkpoint-00350` (step 400 is a documented alternative; see RESULTS.md gap scan) |
| all checkpoints (8, 50-step intervals) | `training/out/adapter-3b-v3c/checkpoint-000{50..500}` |
| eval reports (8 new) | `training/out/report-C00{150,250,350,500}-run3c-{novel,adversarial}.json` |
| demo dataset from the learning pipeline | `training/candidates/build_dataset-20261114-045343-26afc1/` (gitignored) |
| training log | scratchpad `run3c-train.log` |
| VRAM/power samples | scratchpad `run3c-vram.csv` (409 rows, 20 s interval) |

Each checkpoint carries `training_state.pt` with optimizer, scheduler and
RNG state, so any of them is genuinely resumable rather than adapter-weights
only.

## Test results

| suite | count | result |
|---|---|---|
| backend | 491 | pass |
| training | 110 | pass |
| pc-agent | 9 (+9 skipped) | pass |
| web (`node --test`) | 40 | pass |
| ruff (backend, training) | — | clean |

`./scripts/test.sh ios` cannot run here: no Xcode on Windows. Unchanged from
the start of the session and not a regression.

Counts at session start were backend 341, training 74, web 0.

## What was built

- **Objective 9, self-training** — `services/learning.py`,
  `services/training_jobs.py`, `api/learning.py` (10 routes),
  `jarvis_train/build_candidate_dataset.py`. 162 tests.
- **Weather / location / news** — verified live against NWS and a real RSS
  feed, not only fixtures.
- **Voice** — `static/assets/voice.js` extracted and tested under
  `node --test` (40 tests); microphone device picker added.
- **Desktop log path** — `core/desktop_logging.py`; the old path crashed the
  packaged app at import when installed somewhere unwritable.

## Failed or abandoned

| attempt | outcome |
|---|---|
| run 3 | host-memory spill at step 230, 51x slowdown. Checkpoint 200 salvaged. |
| run 3b | identical failure with batch 1 + allocator hints. Fragmentation, not working set. |
| `expandable_segments` | unsupported on Windows; cannot compact allocator segments |
| batch size 2 under the VRAM cap | OOM in 36 s. Correctly, loudly. |
| my "intermediate checkpoint" hypothesis | wrong twice; see the corrections in `RESULTS.md` |

## Blockers needing a person

1. **Promotion decision.** Scoring `eval_sealed` spends it. Everything
   measured so far has been used to *choose* a checkpoint, so the sealed
   split is the only remaining evidence that the gains generalise. Not a
   decision to automate, and this pipeline cannot deploy anyway.
2. **Physical voice verification.** Real device enumeration, echo
   cancellation against real speakers, the Windows permission dialog, audio
   quality from an actual microphone.
3. **iOS/Swift compilation** — impossible on this host.
4. **A rebuilt desktop package** if the log-path fix matters: the existing
   `dist/` build predates it by two months.
5. **News feeds** are unconfigured by design; `get_local_news` is not
   advertised as a capability until `JARVIS_NEWS_FEEDS` is set.

## Resume steps

```bash
# Score the sealed split -- ONLY as the promotion decision.
cd training
./.venv/Scripts/python.exe -m jarvis_train.evaluate \
  --backend hf --model Qwen/Qwen2.5-3B-Instruct \
  --adapter out/adapter-3b-v3c/checkpoint-00350 \
  --split eval_sealed --data data-v3/frozen \
  --batch 8 --max-new-tokens 128 --latency-sample 0 \
  --tag C00350-SEALED

# Paired ASR delta (1,039 rows), does not spend anything held out.
./.venv/Scripts/python.exe -m jarvis_train.evaluate \
  --backend hf --model Qwen/Qwen2.5-3B-Instruct \
  --adapter out/adapter-3b-v3c/checkpoint-00350 \
  --split eval_clean_matched --data data-v3/frozen \
  --batch 8 --max-new-tokens 128 --tag C00350-clean-matched
```

Resuming training from any checkpoint:

```bash
./.venv/Scripts/python.exe -m jarvis_train.train_qlora \
  --train data-v3/train.jsonl --out out/adapter-3b-v3c \
  --resume-from out/adapter-3b-v3c/checkpoint-00350 \
  --max-len 720 --batch-size 1 --grad-accum 16 --vram-fraction 0.82 \
  --save-every 50 --keep-checkpoints 0 --log-every 10
```

**Do not raise `--batch-size` above 1 on this card.** Batch 2 OOMs in 36
seconds under the cap, and without the cap it spills silently into host
memory and runs ~50x slower while `nvidia-smi` still reports 100%
utilisation. Power draw is the honest signal: 66-90 W is real work, ~32 W is
a memory-starved spill.

## Next experiment, if one is wanted

Not another full run. The open question from tonight is narrow: the
adversarial regression between step 350 and 500 suggests the useful stopping
point is before the end of one epoch, on this dataset, at this LoRA rank.
Scoring the existing checkpoints 400 and 450 -- already on disk, no training
needed -- would locate it to within 50 steps for about 20 minutes of GPU
time.
