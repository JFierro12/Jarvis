# Local model training

A QLoRA pipeline that teaches a small local model the JARVIS tool-calling
contract, sized for the RTX 4070 Laptop GPU (8 GiB) this project targets.

## What this does and does not claim

It does **not** make the model know more about the world. Fine-tuning an 8B-class
model on synthetic data cannot add knowledge, and attempting it mostly costs
general reasoning ability.

What it does fix is a specific, measured failure: the local model picking the
wrong tool, or no tool, for ordinary spoken commands. Observed on the deployed
`llama3.1:8b-instruct-q4_K_M` before any training:

| Failure | Example |
|---|---|
| Wrong tool | "launch google" -> `launch_app`, not `open_website` |
| No tool at all | "can you search youtube for how long to boil an egg" -> nothing proposed |
| Tool outside the allowed list | proposed tools absent from `available_tools` |
| Wake word taken as content | a stray "Jarvis." became a Google search query |

Those are format- and intent-adherence problems over a *fixed, known* vocabulary
of 28 tools, which is the case where LoRA genuinely works.

There is a second, independent win. The production system prompt
(`backend/app/services/anthropic_reasoning.py:SYSTEM_PROMPT`) measures **2,055
tokens** and is re-sent on every single turn. The distilled prompt the tuned
model uses is **185 tokens**. That is 1,870 fewer tokens to prefill per voice
turn, which is latency the user hears directly.

## Nothing here invents a capability

`jarvis_train/contract.py` is the guard required by `AGENTS.md`. It does not
restate the tool list; it imports `TOOL_DEFINITIONS` from
`backend/app/tools/registry.py`, and parses `REASON_JSON_SCHEMA` and
`TUNED_SYSTEM_PROMPT` out of the real provider source with `ast`. Every entry
point calls `verify_contract()`, which fails loudly if a target format, site
key, or schema field has drifted from the application.

`requires_confirmation` labels are read from the registry dataclass (which
derives them from the risk tier), never written by hand. Target formats are
cross-checked against the real dispatcher in `backend/app/api/tools.py` --
`control_home_device` does `target.rpartition(" ")`, `open_website` does
`target.partition(" ")`, `move_file` expects `"<src> to <dst>"`, and so on.

The distilled prompt lives in the **backend**, not here, so the text the adapter
was trained against and the text the server sends cannot diverge. Editing it
invalidates the adapter: retrain rather than tweak.

## Why the reported numbers mean something

A random train/test split would put the same phrasing template and the same slot
values on both sides, scoring the model on sentences it had effectively already
seen. The split here is structural:

| Split | Contents | Measures |
|---|---|---|
| `train` | training templates x training values | -- |
| `eval_seen` | same pools, combinations withheld | applying learned phrasings to new fillers |
| `eval_novel` | **withheld templates x withheld values** | wording and entities never seen in any form |

About 20% of templates and 20% of every slot's values are reserved for
`eval_novel` and never appear in training. `eval_novel` is the honest headline
number. The generator prints a split-integrity check, including any surface
strings that collide after voice augmentation -- those are counted *against*
the score rather than hidden.

Inputs are augmented to look like real speech-to-text output, because that is
what the backend receives: lowercase, unpunctuated, wake word often still
attached, fillers and hedges.

## What the grader measures

`evaluate.py` does not report a single accuracy figure, because the errors cost
different amounts:

- `schema_valid` -- a parse failure is a hard outage; the provider raises
  `LanguageReasoningUnavailableError` and the turn dies.
- `target_exact` -- right tool *and* byte-exact target, which is what policy and
  the dispatcher key on.
- `over_action` -- proposed a tool where the answer was none. **The expensive
  error**: acting when it should have asked.
- `under_action` -- proposed nothing when a tool was wanted. Annoying, not dangerous.
- `injection_obeyed` -- proposed a tool in response to an untrusted-context
  attack. Must be zero.
- `offered_unavailable` / `unknown_tool` -- proposed a tool outside
  `available_tools` or outside the registry. Must be zero.

`confirm_match` is reported but is **cosmetic, not a safety metric**. Grepping
the consumers shows the model's `requires_confirmation` field is read by no
client: `backend/app/tools/policy.py` decides confirmation from the registry's
risk tier independently of anything the model says. Training the field to agree
with the registry makes the response self-consistent and nothing more. Any
improvement in it must not be presented as a security gain.

Targets are also split into two kinds, because exact match means different
things for each. A **closed-vocabulary** target (site key, entity id, app or
file name) is genuinely right or wrong. A **free-text** target
(`take_screenshot`, `save_note`, `save_memory`, `create_reminder`,
`search_memory`, `open_navigation`) is prose passed to another component, where
byte-exact match largely measures whether the model learned this app's phrasing
convention -- something a fine-tune picks up trivially and a base model cannot
guess. Both buckets are printed; the closed-vocabulary figure is the meaningful
one for comparing models.

Roughly 32% of training rows correctly propose **no** tool. The first balanced
draft came out 95% tool-proposing, which is exactly the bias that turns a stray
"Jarvis." into a search query.

But the *composition* of those no-tool rows matters as much as their count, and
getting it wrong cost a whole training run. In run 1, about 1,070 of ~1,220
no-tool rows were refusals (`injection`, `unavailable`, `unclear`,
`limitation`) and only `football` ever showed the model answering a question
helpfully without calling anything. It learned exactly that: asked for the
capital of France, the tuned adapter replied *"I don't have access to that
here, sir."* The `hard:general` family exists to break the association between
"no tool" and "refuse". It is not there to teach facts -- a 3B model already
knows Paris, and fine-tuning cannot install knowledge.

### The forgetting probe must stay out of distribution

`probe_general.py` is only a measurement if the model has not been trained on
its questions. The first draft of `hard:general` copied them wholesale -- 15 of
16 exact matches -- which would have produced a probe reporting a perfect fix
while testing nothing but memorisation. The questions now differ, and
`tests/test_dataset_contract.py::test_the_general_probe_questions_never_appear_in_training_data`
fails the build if the overlap returns. Shared *topics* are intended; shared
strings are not.

### Few templates make a family's score a coin flip

The structural holdout reserves every fifth template (`index % 5 == 4`). An
intent with five templates therefore puts exactly one phrasing in
`eval_novel`, and that family's whole score depends on generalising to that
single wording. Run 1 lost all 16 `screenshot` rows to one unseen prefix and 14
of 16 `move_file` rows to the unseen verb "drag". The weak intents now carry
ten templates each. Variance, not just capability, was being measured.

The grader salvages JSON out of surrounding prose before scoring, which makes
the untuned baseline look *better* than a strict parse would. That is
deliberate: the comparison should not flatter the fine-tune.

## Model choice

Default base is **Qwen2.5-3B-Instruct**: Apache-2.0 and ungated, so no HF token
or license click is needed. For narrow structured extraction a tuned 3B is both
more reliable and roughly 2x faster at inference than the 8B it replaces, and
inference latency is what a voice assistant is judged on.

7B fits this card only barely under QLoRA, and the Windows compositor shares the
same 8 GiB. `--model Qwen/Qwen2.5-7B-Instruct` is supported; peak VRAM is
measured and logged rather than assumed.

## Running it

```bash
cd training
python -m jarvis_train.generate --per-intent 130      # writes data/*.jsonl

# Baseline the currently deployed model, as the app actually calls it
./.venv/Scripts/python.exe -m jarvis_train.evaluate \
    --backend ollama --model llama3.1:8b-instruct-q4_K_M \
    --split eval_novel --system production

# Train (free the GPU first: Ollama holds ~5 GiB)
ollama stop llama3.1:8b-instruct-q4_K_M
./.venv/Scripts/python.exe -m jarvis_train.train_qlora

# Score the adapter the same way
./.venv/Scripts/python.exe -m jarvis_train.evaluate \
    --backend hf --model Qwen/Qwen2.5-3B-Instruct \
    --adapter out/adapter --split eval_novel --system tuned

# Merge and register with Ollama (see the caveat below -- the import currently
# fails on this Ollama version for Qwen2)
./.venv/Scripts/python.exe -m jarvis_train.export --name jarvis-router
```

### Ollama will not import Qwen2 safetensors -- go through GGUF

Measured against ollama 0.40.16, three routes are closed and one works:

| route | result |
|---|---|
| `ollama create -q q4_K_M` (safetensors) | rejected -- Ollama's names are `int4`/`int8`/`nvfp4`/`mxfp4`/`mxfp8` |
| `ollama create -q int4` (safetensors) | `unsupported MLX architecture: model "Qwen2ForCausalLM"` |
| `FROM qwen2.5:3b-instruct` + `ADAPTER` | `Error: LoRA adapters are no longer supported` |
| **GGUF import** | **works** |

So the merge is fine and the safetensors import is not. Convert first, with
llama.cpp's `convert_hf_to_gguf.py` (a shallow clone -- current master splits it
across a `conversion/` package), then import the GGUF:

```bash
python convert_hf_to_gguf.py out/merged --outtype f16     --outfile out/gguf/jarvis-router-f16.gguf
./.venv/Scripts/python.exe -m jarvis_train.export --skip-merge     --from-gguf out/gguf/jarvis-router-f16.gguf --quantize ""
```

`--quantize ""` is required: `ollama create -q` works only for safetensors
imports ("quantize GGUF models before importing"), so a GGUF goes in at
whatever precision it was converted at. Converting with `--outtype q8_0`
instead of `f16` is the cheap way to a ~3.3 GiB model; `q4_K_M` needs a
compiled `llama-quantize`.

The merge loads the base in **fp16 on CPU**, not the 4-bit form training used.
Merging LoRA deltas into dequantised 4-bit weights bakes quantisation error in
permanently and then quantises again on import -- one lossy step is enough.

## Using it in the app

```ini
JARVIS_REASONING_PROVIDER=local
JARVIS_LOCAL_LLM_MODEL=jarvis-router
JARVIS_LOCAL_LLM_PROMPT_PROFILE=tuned
```

`local_llm_prompt_profile` defaults to `production` and must be opted into.
Sending the short prompt to a model that was never tuned on it degrades tool
choice, so an unrecognised value falls back to the full prompt rather than
guessing.

The trust boundary is unchanged. The model still only ever proposes
`{tool_name, target, content}`; `arguments` stays schema-locked to `{}`;
`PolicyEngine` / `evaluate_tool_call` remains the only authority on whether a
call executes, and the backend re-checks policy at execute time. A fine-tune
makes proposals better, never authorised -- see `docs/THREAT_MODEL.md`.

## Limits

- Training data is synthetic. It teaches the contract and the phrasings in
  `intents.py`; it is not a sample of real user speech, and real usage will
  contain shapes it has never seen.
- `eval_novel` holds out templates and slot values, not *concepts*. A genuinely
  new way of asking for something remains untested.
- Every number in `out/report-*.json` comes from these generated splits. None of
  it is a claim about real-world accuracy, latency under load, or injection
  immunity against an adaptive attacker.
- Prompt-level injection defence is defence in depth. The deterministic policy
  tests in `backend/tests/test_prompt_injection.py` are what actually hold, and
  they do not depend on generated text.
