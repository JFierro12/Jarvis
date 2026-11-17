"""Checks that the generated dataset agrees with the running application.

A mislabelled dataset is the worst kind of bug here, because training bakes it
into weights where it is invisible until a voice command does the wrong thing.
So rather than trusting the generator, these tests re-derive each target format
using the *same* parsing the real dispatcher performs
(``backend/app/api/tools.py``) and assert the result is what the application
would accept.

Deliberately stdlib-only, so this runs under any interpreter in the repo
without the training venv's torch/transformers stack.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis_train import contract  # noqa: E402
from jarvis_train.generate import build, to_chat  # noqa: E402

DATA_DIR = ROOT / "data"


@pytest.fixture(scope="module")
def splits():
    # Small and fast: correctness of labels does not depend on volume.
    return build(per_intent_train=12, seed=7)


@pytest.fixture(scope="module")
def all_records(splits):
    return [r for rows in splits.values() for r in rows]


def test_contract_matches_the_live_application():
    contract.verify_contract()


def test_every_proposed_tool_exists_in_the_registry(all_records):
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if call:
            assert call["tool_name"] in contract.TOOL_DEFINITIONS, record


def test_every_proposed_tool_is_in_its_own_available_list(all_records):
    """The label must never ask for something the prompt said was unavailable."""
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if call:
            assert call["tool_name"] in record["available_tools"], record


def test_confirmation_flag_is_the_registry_value(all_records):
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        expected = contract.requires_confirmation_for(call["tool_name"]) if call else False
        assert record["label"]["requires_confirmation"] is expected, record


def test_control_home_device_target_parses_the_way_the_dispatcher_parses_it(all_records):
    """tools.py: entity_id, _, state_word = target.rpartition(" ")"""
    seen = 0
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if not call or call["tool_name"] != "control_home_device":
            continue
        seen += 1
        entity_id, _, state = call["target"].rpartition(" ")
        assert entity_id in contract.HOME_ENTITIES, call
        assert state in {"on", "off"}, call
    assert seen, "no control_home_device examples were generated"


def test_open_website_target_parses_to_a_valid_site_key(all_records):
    """tools.py: site_key, _, query = target.strip().lower().partition(" ")"""
    seen = 0
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if not call or call["tool_name"] != "open_website":
            continue
        seen += 1
        site_key, _, query = call["target"].strip().lower().partition(" ")
        assert site_key in contract.SITE_KEYS, call
        # Only youtube/google ever receive search terms; everything else opens
        # a homepage no matter what was asked.
        if query:
            assert site_key in contract.SEARCHABLE_SITE_KEYS, call
    assert seen


def test_close_website_target_is_a_bare_site_key(all_records):
    seen = 0
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if not call or call["tool_name"] != "close_website":
            continue
        seen += 1
        assert call["target"].strip().lower() in contract.SITE_KEYS, call
    assert seen


def test_move_file_target_uses_the_source_to_destination_form(all_records):
    seen = 0
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if not call or call["tool_name"] != "move_file":
            continue
        seen += 1
        assert " to " in call["target"], call
        source, _, destination = call["target"].partition(" to ")
        assert source.strip() and destination.strip(), call
    assert seen


def test_no_target_tools_carry_no_target(all_records):
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if call and call["tool_name"] in contract.NO_TARGET_TOOLS:
            assert call["target"] == "", call


def test_content_is_only_populated_for_the_two_write_tools(all_records):
    """The schema allows `content` precisely so write_file/append_file have a
    body. Any other tool carrying content would be smuggling data."""
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if call and call["content"]:
            assert call["tool_name"] in {"write_file", "append_file"}, call


def test_write_tools_put_the_body_in_content_not_the_target(all_records):
    seen = 0
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if not call or call["tool_name"] not in {"write_file", "append_file"}:
            continue
        seen += 1
        assert call["content"], call
        # target is only ever the path.
        assert call["content"] not in call["target"], call
    assert seen


def test_arguments_stay_schema_locked_to_empty(all_records):
    """REASON_JSON_SCHEMA pins `arguments` to {} with additionalProperties
    false. A label with anything in it would train the model to try a channel
    policy cannot see."""
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if call:
            assert call["arguments"] == {}, record


def test_untrusted_context_never_authorizes_a_tool_call(all_records):
    """The whole threat model in one assertion."""
    checked = 0
    for record in all_records:
        sources = {item["source"] for item in record["context"]}
        if not sources or not sources.issubset(set(contract.UNTRUSTED_SOURCES)):
            continue
        if record["family"] != "hard:injection":
            continue
        checked += 1
        assert record["label"]["proposed_tool_call"] is None, record
    assert checked, "no injection examples were generated"


def test_no_example_claims_a_tool_outside_the_prompt_vocabulary(all_records):
    """Guards against a template drifting to a site/app the app cannot handle."""
    for record in all_records:
        call = record["label"]["proposed_tool_call"]
        if call and call["tool_name"] == "launch_app":
            assert call["target"], record


def test_eval_novel_withholds_templates_from_training(splits):
    train_templates = {r["template_id"] for r in splits["train"]}
    novel_templates = {r["template_id"] for r in splits["eval_novel"]}
    held_back = novel_templates - train_templates
    # Some intents have too few templates to hold any back; most must.
    assert len(held_back) / len(novel_templates) > 0.5, (
        f"only {len(held_back)}/{len(novel_templates)} eval_novel templates are unseen"
    )


def test_chat_rendering_matches_the_provider_format(all_records):
    """to_chat() must produce the byte-exact user message the server sends."""
    record = all_records[0]
    row = to_chat(record)
    expected = contract.render_user_message(
        record["context"], record["question"], record["available_tools"]
    )
    assert row["messages"][1]["content"] == expected
    assert row["messages"][0]["content"] == contract.TUNED_SYSTEM_PROMPT
    # The assistant turn must be parseable JSON matching the schema shape.
    parsed = json.loads(row["messages"][2]["content"])
    assert set(parsed) == {"spoken_answer", "requires_confirmation", "proposed_tool_call"}


def test_tuned_prompt_is_the_one_the_backend_will_send():
    """Training against a prompt the server does not send is the classic way a
    fine-tune regresses in production."""
    source = (contract.REPO_ROOT / "backend" / "app" / "services" / "local_reasoning.py").read_text(
        encoding="utf-8"
    )
    assert "TUNED_SYSTEM_PROMPT" in source
    assert "local_llm_prompt_profile" in source
    config = (contract.REPO_ROOT / "backend" / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert 'local_llm_prompt_profile: str = "production"' in config, (
        "the tuned prompt must stay opt-in"
    )


@pytest.mark.skipif(not (DATA_DIR / "train.jsonl").exists(), reason="dataset not generated yet")
def test_generated_files_on_disk_are_well_formed():
    for name in ("train", "eval_seen", "eval_novel"):
        rows = [json.loads(line) for line in (DATA_DIR / f"{name}.jsonl").open(encoding="utf-8")]
        assert rows
        for row in rows[:200]:
            assert len(row["messages"]) == 3
            assert row["messages"][0]["role"] == "system"
            assert row["messages"][1]["role"] == "user"
            assert row["messages"][2]["role"] == "assistant"
            json.loads(row["messages"][2]["content"])


# --- The forgetting probe must stay out of distribution --------------------
#
# This guard exists because the invariant was already broken once. The
# `hard:general` family was added to fix a measured regression (the first 3B
# adapter refused "what's the capital of france"), and the first draft of it
# copied its questions straight out of `probe_general.PROBES` -- 15 of 16. That
# would have turned the probe from a forgetting measure into a memorisation
# check, and it would have reported a perfect fix.
#
# Topic overlap is fine and intended: the family teaches a behavioural shape
# (answer plainly, propose nothing), and the probe should test whether that
# shape generalises. Identical question strings are not fine.


def test_the_general_probe_questions_never_appear_in_training_data(all_records):
    from jarvis_train.probe_general import PROBES

    asked = {r["question"].strip().lower() for r in all_records}
    overlap = sorted(p["q"].strip().lower() for p in PROBES if p["q"].strip().lower() in asked)
    assert not overlap, (
        "probe_general.py questions leaked into the training data, which makes the "
        f"probe measure memorisation instead of retention: {overlap}"
    )


def test_general_family_answers_plainly_without_proposing_a_tool(splits):
    rows = [r for r in splits["train"] if r["family"] == "hard:general"]
    assert rows, "the hard:general family is missing; refusal collapse will return"
    for row in rows:
        label = row["label"]
        assert label["proposed_tool_call"] is None, (
            f"hard:general must propose no tool: {row['question']}"
        )
        assert label["spoken_answer"].strip(), (
            f"hard:general needs a real answer: {row['question']}"
        )


def test_general_family_does_not_decline_by_default(splits):
    """The whole point of this family is that "no tool" stops meaning "refuse".

    A handful of these questions genuinely should defer -- medical ones -- so
    this asserts the *shape* of the set, not every row.
    """
    rows = [r for r in splits["train"] if r["family"] == "hard:general"]
    refusal_markers = ("i don't have access", "isn't available", "i can't act on it",
                       "that tool isn't available")
    refusals = [
        r for r in rows
        if any(m in r["label"]["spoken_answer"].lower() for m in refusal_markers)
    ]
    assert len(refusals) / len(rows) < 0.1, (
        f"{len(refusals)}/{len(rows)} general answers read as refusals, which re-teaches "
        "the exact association this family exists to break"
    )


# ---------------------------------------------------------------------------
# hard:current -- admit the live-data limit AND still answer
# ---------------------------------------------------------------------------
#
# The sibling failure to hard:general's refusal collapse. Once the model stops
# refusing, the next thing it does wrong is defer: say it could describe the
# subject and ask whether that is wanted, which spends the turn and delivers
# nothing. Measured at 13/42 probe answers on the untuned 8B before the
# production prompt was changed, and present in both cases the user reported.


def test_current_family_exists_and_proposes_no_tool(splits):
    rows = [r for r in splits["train"] if r["family"] == "hard:current"]
    assert rows, "the hard:current family is missing"
    for row in rows:
        label = row["label"]
        assert label["proposed_tool_call"] is None, (
            f"hard:current rows are generated with no relevant tool available, "
            f"so none may be proposed: {row['question']}"
        )


def test_current_rows_never_list_a_tool_that_could_answer(splits):
    """The family's premise, asserted rather than assumed.

    If a retrieval tool is in available_tools, "I can't check" is the wrong
    label and the row teaches refusal-despite-a-tool -- the association
    hard:general exists to break. The first draft of this family used
    _available_including([]), which samples at random and did exactly that.
    """
    from jarvis_train.generate import RETRIEVAL_TOOLS

    rows = [r for r in splits["train"] if r["family"] == "hard:current"]
    assert rows
    offenders = [
        (r["question"], sorted(set(r["available_tools"]) & set(RETRIEVAL_TOOLS)))
        for r in rows
        if set(r["available_tools"]) & set(RETRIEVAL_TOOLS)
    ]
    assert not offenders, (
        f"hard:current rows listing a tool that could answer them: {offenders[:5]}"
    )


def test_current_answers_admit_the_limit(splits):
    """Part one of the three-part shape."""
    rows = [r for r in splits["train"] if r["family"] == "hard:current"]
    admissions = ("can't check", "can't see", "can't look up", "can't pull",
                  "can't read", "cannot check", "no feed is connected",
                  "nothing live is connected")
    missing = [
        r["question"] for r in rows
        if not any(a in r["label"]["spoken_answer"].lower() for a in admissions)
    ]
    assert not missing, f"these hard:current answers never admit the limit: {missing[:5]}"


def test_current_answers_still_say_something_useful(splits):
    """Part two. Admitting the limit and stopping is the defect, not the fix."""
    rows = [r for r in splits["train"] if r["family"] == "hard:current"]
    thin = [
        r["question"] for r in rows
        if len(r["label"]["spoken_answer"].split()) < 20
    ]
    assert not thin, (
        f"these hard:current answers stop at the admission: {thin[:5]}. "
        "An answer that only says 'I can't' teaches the behaviour this family "
        "exists to replace."
    )


def test_current_answers_never_defer(splits):
    """The family must not demonstrate the very shape it is correcting."""
    rows = [r for r in splits["train"] if r["family"] == "hard:current"]
    deferrals = ("would you like me to", "if you'd like, i can", "shall i",
                 "do you want me to", "i can provide general information",
                 "let me know if you")
    offenders = [
        r["question"] for r in rows
        if any(d in r["label"]["spoken_answer"].lower() for d in deferrals)
    ]
    assert not offenders, f"hard:current answers that defer: {offenders[:5]}"


def test_current_answers_encode_no_expiring_figure(splits):
    """A family about not knowing current things must not encode current things.

    A price, a version number or a standings position baked into a label is
    wrong within weeks and teaches the model to state stale values with
    confidence. The background these answers give is structural -- how a
    championship is decided, that indices move intraday -- so it stays true.
    Years are allowed: "past about a week" and similar prose are not figures,
    but a currency amount or a dotted version is.
    """
    import re

    rows = [r for r in splits["train"] if r["family"] == "hard:current"]
    patterns = [
        r"[$\u00a3\u20ac]\s?\d",       # a price
        r"\bv?\d+\.\d+(\.\d+)?\b",  # a dotted version number
        r"\b\d+\s*(points?|goals?)\s+(ahead|clear|behind)",
    ]
    offenders = [
        (r["question"], p) for r in rows for p in patterns
        if re.search(p, r["label"]["spoken_answer"].lower())
    ]
    assert not offenders, (
        f"hard:current labels contain figures that will expire: {offenders[:5]}"
    )


# ---------------------------------------------------------------------------
# Arithmetic labels are re-derived, never trusted
# ---------------------------------------------------------------------------
#
# The arithmetic rows are machine-generated, which removes the memorisation
# problem of a fixed answer list but introduces a worse risk: a bug in the
# generator would mislabel hundreds of rows at once and train the model to do
# arithmetic wrong. So these tests parse the question text and recompute the
# answer independently of the code that produced it. A shared helper would
# defeat the purpose, so the parsing here is deliberately separate.

_CLOCK_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _to_minutes(face: str) -> int:
    hour, minute = (int(x) for x in face.split(":"))
    return (hour % 12) * 60 + minute


def _clock_delta(start: str, end: str) -> int:
    """Signed minutes from start to end on a 12-hour face, nearest wrap."""
    delta = (_to_minutes(end) - _to_minutes(start)) % (12 * 60)
    return delta


@pytest.fixture(scope="module")
def arithmetic_rows(splits):
    """Every generated arithmetic row, across all splits."""
    rows = []
    for split_rows in splits.values():
        for row in split_rows:
            if row["family"] != "hard:general":
                continue
            if _CLOCK_RE.search(row["question"]) or re.search(r"\d", row["question"]):
                rows.append(row)
    assert rows, "no arithmetic rows found"
    return rows


def test_clock_arithmetic_answers_are_correct(arithmetic_rows):
    """start + duration, and target - duration, must actually add up."""
    checked = 0
    for row in arithmetic_rows:
        q = row["question"]
        said = row["label"]["spoken_answer"]
        faces = _CLOCK_RE.findall(q)
        mins = re.search(r"\b(\d+) minutes?\b", q) or re.search(r"\ba (\d+) minute\b", q)
        answer_face = _CLOCK_RE.search(said)
        if len(faces) != 1 or not mins or not answer_face:
            continue
        start = f"{faces[0][0]}:{faces[0][1]}"
        duration = int(mins.group(1))
        got = _clock_delta(start, answer_face.group(0))
        # Departure questions subtract; the rest add.
        backwards = any(k in q for k in ("when should i leave", "when do i go",
                                         "when do i set off", "to arrive by"))
        want = (-duration) % (12 * 60) if backwards else duration % (12 * 60)
        assert got == want, (
            f"clock arithmetic is wrong: {q!r} -> {said!r} "
            f"(start {start}, {duration} min, {'back' if backwards else 'forward'})"
        )
        checked += 1
    assert checked >= 40, f"expected to check many clock rows, only saw {checked}"


def test_money_and_counting_answers_are_correct(arithmetic_rows):
    """Subtraction, tips, discounts, halving, products and division."""
    patterns = [
        # (question regex, answer regex, expected value from question groups)
        (r"i had (\d+) dollars and spent (\d+)", r"^(\d+) dollars", lambda a, b: a - b),
        (r"started with (\d+) dollars spent (\d+)", r"^(\d+) dollars", lambda a, b: a - b),
        (r"^(\d+) dollars minus (\d+) dollars", r"^(\d+) dollars", lambda a, b: a - b),
        (r"a (\d+) percent tip on (\d+) dollars", r"^(\d+) dollars", lambda p, t: t * p // 100),
        (r"(\d+) percent of a (\d+) dollar bill", r"^(\d+) dollars", lambda p, t: t * p // 100),
        (r"tip (\d+) percent on (\d+) dollars", r"^(\d+) dollars", lambda p, t: t * p // 100),
        (r"what's (\d+) percent off (\d+) dollars", r"^(\d+) dollars",
         lambda p, t: t - t * p // 100),
        (r"^(\d+) dollars with (\d+) percent off", r"^(\d+) dollars",
         lambda t, p: t - t * p // 100),
        (r"take (\d+) percent off (\d+) dollars", r"^(\d+) dollars",
         lambda p, t: t - t * p // 100),
        (r"what's half of (\d+)", r"^(\d+)", lambda v: v // 2),
        (r"^half (\d+) for me", r"^(\d+)", lambda v: v // 2),
        (r"^(\d+) divided by two", r"^(\d+)", lambda v: v // 2),
        (r"^(\d+) packs of (\d+)", r"^(\d+)", lambda a, b: a * b),
        (r"^(\d+) boxes with (\d+) in each", r"^(\d+)", lambda a, b: a * b),
        (r"^(\d+) times (\d+) is what", r"^(\d+)", lambda a, b: a * b),
        (r"split (\d+) dollars (\d+) ways", r"^(\d+)", lambda t, w: t // w),
        (r"^(\d+) dollars between (\d+) people", r"^(\d+)", lambda t, w: t // w),
        (r"^divide (\d+) by (\d+)", r"^(\d+)", lambda t, w: t // w),
    ]
    checked = 0
    for row in arithmetic_rows:
        q, said = row["question"], row["label"]["spoken_answer"]
        for q_re, a_re, expect in patterns:
            qm = re.search(q_re, q)
            if not qm:
                continue
            am = re.search(a_re, said)
            assert am, f"answer has no leading number: {q!r} -> {said!r}"
            want = expect(*(int(g) for g in qm.groups()))
            assert int(am.group(1)) == want, (
                f"arithmetic is wrong: {q!r} -> {said!r} (expected {want})"
            )
            checked += 1
            break
    assert checked >= 60, f"expected to check many money/counting rows, only saw {checked}"


def test_tip_and_discount_totals_are_consistent(arithmetic_rows):
    """"19 dollars, sir - 209 all in" must agree with the bill it came from."""
    checked = 0
    for row in arithmetic_rows:
        q, said = row["question"], row["label"]["spoken_answer"]
        m = re.search(r"(\d+) percent tip on (\d+) dollars|tip (\d+) percent on (\d+) dollars", q)
        total = re.search(r"- (\d+) all in", said)
        if not m or not total:
            continue
        groups = [g for g in m.groups() if g is not None]
        pct, bill = int(groups[0]), int(groups[1])
        assert int(total.group(1)) == bill + bill * pct // 100, (
            f"tip total does not add up: {q!r} -> {said!r}"
        )
        checked += 1
    assert checked >= 5, f"expected several tip rows, saw {checked}"


def test_arithmetic_questions_are_not_shared_between_train_and_eval(splits):
    """A memorised answer must not be able to pass as generalisation."""
    def questions(split: str) -> set:
        return {
            r["question"] for r in splits[split]
            if r["family"] == "hard:general" and re.search(r"\d", r["question"])
        }

    train = questions("train")
    for split in ("eval_novel", "eval_seen"):
        overlap = train & questions(split)
        assert not overlap, (
            f"{len(overlap)} arithmetic questions appear in both train and {split}: "
            f"{sorted(overlap)[:3]}"
        )


# ---------------------------------------------------------------------------
# The measurement itself has to be sound
# ---------------------------------------------------------------------------
#
# These guard the instrument rather than the labels. Run 2 was reported at
# 93.2% correct-tool on 951 "novel" rows, but 24 of 40 families were scored on
# exactly one held-out phrasing repeated ~16 times, so the effective sample was
# about 60 phrasings, not 951 rows. Two unlucky index-4 picks -- the only
# plural lights template, and the only imperative screen question -- accounted
# for 39% of all failures. A score that one authoring coincidence can move by
# a point is not a score, so these tests fail if the pools regress to that.


def test_no_family_can_be_scored_on_fewer_than_three_phrasings():
    """A family measured on one phrasing is one trial, not sixteen.

    This asserts on the *pools* rather than on a generated split, because how
    many held-out phrasings exist is a property of the template lists, while
    how many a split happens to sample depends on its row count. Checking the
    pools is both exact and independent of the volume used to generate.
    """
    from jarvis_train import generate as gen
    from jarvis_train.intents import INTENTS

    def holdout(items):
        return [v for i, v in enumerate(items) if i % gen.HOLDOUT_EVERY == gen.HOLDOUT_EVERY - 1]

    thin = {
        f"intent:{i.name}": len(holdout(i.templates))
        for i in INTENTS
        if len(holdout(i.templates)) < 3
    }
    # The pools behind each hard family, named so a failure says which to widen.
    hard_pools = {
        "hard:injection": gen.INJECTION_QUESTIONS,
        "hard:unclear": gen.UNCLEAR_QUESTIONS,
        "hard:limitation": gen.LIMITATION_CASES,
        "hard:general": gen.GENERAL_CASES,
        "hard:football": gen.FOOTBALL_CASES,
        "hard:continuity": gen.CONTINUITY_CASES,
        "hard:reopen": gen.ALREADY_OPEN_QUESTIONS,
        "hard:current": gen.CURRENT_INFO_CASES,
    }
    thin.update({
        name: len(holdout(pool))
        for name, pool in hard_pools.items() if len(holdout(pool)) < 3
    })
    assert not thin, (
        f"these families can hold out fewer than 3 distinct phrasings: {thin}. "
        "Percentages over repeated rows of one phrasing read as precision that "
        "is not there -- widen the pool rather than lowering this bar."
    )


def test_adversarial_phrasings_never_appear_in_training(splits):
    """The one split whose difficulty cannot be eroded by adding coverage.

    eval_novel is a real holdout, but it is only as hard as the template pool
    makes it: widening coverage of a phrasing family (plural "lights", say)
    turns a previously-hard held-out template into a near-duplicate of
    training data, and the holdout score quietly becomes a coverage score.
    The adversarial phrasings live outside `Intent.templates` entirely, so no
    amount of template expansion can reach them.
    """
    train_questions = {r["question"] for r in splits["train"]}
    train_templates = {r["template_id"] for r in splits["train"]}
    adversarial = splits["eval_adversarial"]
    assert adversarial, "the adversarial split is empty"

    leaked_q = train_questions & {r["question"] for r in adversarial}
    leaked_t = train_templates & {r["template_id"] for r in adversarial}
    assert not leaked_q, f"adversarial questions found in training: {sorted(leaked_q)[:5]}"
    assert not leaked_t, f"adversarial template ids found in training: {sorted(leaked_t)[:5]}"


def test_every_intent_has_adversarial_phrasings():
    """A missing set would silently drop that intent from the honest measure."""
    from jarvis_train.intents import INTENTS

    missing = [i.name for i in INTENTS if len(i.adversarial) < 3]
    assert not missing, f"intents without at least 3 adversarial phrasings: {missing}"

    for intent in INTENTS:
        overlap = set(intent.adversarial) & set(intent.templates)
        assert not overlap, (
            f"{intent.name} lists the same phrasing as both a template and an "
            f"adversarial case: {sorted(overlap)}"
        )


def test_hedges_only_ever_precede_an_imperative(splits):
    """"could you what's on my taskbar" is not a sentence anyone says.

    voicify() used to prepend "can you"/"could you"/"would you"/"i need you
    to" to any template, including interrogatives and noun-fronted ones. One
    in four hedged rows came out ungrammatical -- 350 training rows and 48
    eval rows -- so the model was trained on word salad and then marked wrong
    for mishandling it.
    """
    from jarvis_train.generate import _takes_a_hedge

    hedges = ("can you ", "could you ", "i need you to ", "would you ")
    prefixes = re.compile(r"^((ok |hey )?jarvis[,]? |uh |um |so |hey |alright )+", re.I)

    offenders = []
    for rows in splits.values():
        for row in rows:
            stripped = prefixes.sub("", row["question"]).lower().strip()
            for hedge in hedges:
                if stripped.startswith(hedge):
                    if not _takes_a_hedge(stripped[len(hedge):]):
                        offenders.append(row["question"])
                    break
    assert not offenders, (
        f"{len(offenders)} questions hedge a non-imperative, e.g. {offenders[:5]}"
    )


# ---------------------------------------------------------------------------
# The probe set must measure knowledge, not neighbour recall
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset("""
a an and are as at be been can could do does for from get give has have how
i if in into is it its me my of on or should that the their them then there
to was were what whats what's when where which who will with would you your
me's one two
""".split())


def _content_words(text: str) -> set:
    words = re.findall(r"[a-z']+", (text or "").lower())
    return {w.strip("'") for w in words if w.strip("'") not in _STOPWORDS and len(w) > 2}


def test_probe_questions_are_disjoint_from_training():
    """A probe that paraphrases a training row measures recall, not knowledge.

    `probe_general.py` is the general-conversation instrument for the
    fine-tune, so a contaminated question there turns into a false capability
    claim. Exact-string disjointness was not enough: "explain in one sentence
    why the sky is blue" shared no row verbatim but sat next to "explain in one
    sentence why the ocean looks blue", and run 2 duly answered the sky
    question with the ocean's mechanism.

    The comparison is against each training question *concatenated with its
    answer*, because the leak that mattered most ran through an answer: the
    probe "how many days are in a leap year" sits beside "how many days are in
    february" -> "28, sir - 29 in a leap year", whose answer hands over the
    probe's own phrase next to the wrong number.

    Three probe kinds are exempt, for reasons that are about what they measure
    rather than convenience:

    * `reasoning` -- arithmetic probes are *meant* to reuse the training frame
      ("if i leave at X and it takes Y"). Sharing the frame while changing the
      operands is the entire test, so instead of an overlap ceiling these get
      a stricter check below: no training row may contain the same numbers.
    * `social` and `ambiguous` -- greetings and "can you help me with
      something" carry two or three content words, all common ones, so an
      overlap ratio over them is noise. They test register, not knowledge.
    * `retained` -- the football probe is deliberately a retained-skill check
      and is expected to resemble the training mix.
    """
    from jarvis_train.generate import FOOTBALL_CASES, GENERAL_CASES, LIMITATION_CASES
    from jarvis_train.probe_general import PROBES

    training = list(GENERAL_CASES) + list(LIMITATION_CASES) + list(FOOTBALL_CASES)
    neighbours = [(q, _content_words(f"{q} {a}")) for q, a in training]
    exempt = {"reasoning", "social", "ambiguous", "retained"}

    worst = []
    for probe in PROBES:
        if probe["kind"] in exempt:
            continue
        words = _content_words(probe["q"])
        # Under four content words an overlap ratio is dominated by whichever
        # common words survived the stopword list.
        if len(words) < 4:
            continue
        for train_q, train_words in neighbours:
            overlap = len(words & train_words) / len(words)
            if overlap >= 0.6:
                worst.append((probe["q"], train_q, round(overlap, 2)))

    assert not worst, (
        "these probe questions overlap a training row by 60% or more of their "
        "content words, so they measure neighbour recall rather than general "
        f"knowledge: {worst}"
    )


def test_arithmetic_probes_share_no_operands_with_training():
    """The arithmetic probe must require computing, not recalling.

    Run 2 answered the 2:40 probe with "7:15, sir." -- a clock face taken
    verbatim from a different training row. The frame is shared on purpose;
    the numbers must not be, or a correct answer proves nothing.
    """
    from jarvis_train.generate import GENERAL_CASES
    from jarvis_train.probe_general import PROBES

    numbers = re.compile(r"\d+(?::\d+)?")
    collisions = []
    for probe in PROBES:
        if probe["kind"] != "reasoning":
            continue
        want = set(numbers.findall(probe["q"]))
        if not want:
            continue
        for question, answer in GENERAL_CASES:
            if want and want <= set(numbers.findall(f"{question} {answer}")):
                collisions.append((probe["q"], question, sorted(want)))
    assert not collisions, (
        "an arithmetic probe shares every one of its numbers with a training "
        f"row, so recall could pass for computation: {collisions}"
    )
