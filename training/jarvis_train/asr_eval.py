"""A paired eval split that corrupts questions the way a transcriber does.

`voicify()` already varies hedges, fillers, wake words, casing and terminal
punctuation. Those are formatting differences, and the model handles them.
They are not what speech recognition actually gets wrong. A real transcript
substitutes near-homophones, splits or joins compounds, drops unstressed
function words, and spells digits out -- and this assistant is voice-first, so
every production request arrives through exactly that channel.

Design decisions worth stating:

* **Eval-only.** Nothing here is added to training, so freezing is unaffected
  and the run-3 dataset hash stays valid. This measures robustness; it does
  not try to buy it.
* **Paired.** Each row is a corruption of one specific frozen eval row, with
  the context, available tools and label left byte-identical. The user said
  the same thing; the transcriber mangled it. So accuracy here minus accuracy
  on the clean twin isolates the cost of transcription noise with phrasing,
  slot values and difficulty all held constant. An unpaired noisy set could
  not distinguish "noise hurts" from "these happened to be harder rows".
* **Derived from held-out rows only**, so no training phrasing leaks in by the
  back door.
* **Conservative and seeded.** Every rule is a substitution a transcriber
  demonstrably makes. The temptation is to corrupt harder and get a more
  dramatic number, which would repeat the `voicify()` mistake of grading the
  model on input no human ever produced.

    python -m jarvis_train.asr_eval --frozen data-v3/frozen --seed 20261112
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jarvis_train.freeze import row_id

# Near-homophones a transcriber genuinely confuses. Keyed on whole words so
# "lighten" is never rewritten through the "light" rule.
HOMOPHONES: Dict[str, str] = {
    "light": "lite",
    "lights": "lites",
    "write": "right",
    "close": "clothes",
    "to": "too",
    "for": "four",
    "there": "their",
    "their": "there",
    "whats": "what's",
    "know": "no",
    "new": "knew",
    "wait": "weight",
    "by": "buy",
    "one": "won",
    "two": "too",
    "four": "for",
    "eight": "ate",
    "hear": "here",
    "here": "hear",
    "read": "red",
    "mail": "male",
    "week": "weak",
    "some": "sum",
    "sent": "cent",
    "would": "wood",
    "our": "hour",
}

# Compounds and initialisms a transcriber splits apart.
SPLITS: Dict[str, str] = {
    "youtube": "you tube",
    "screenshot": "screen shot",
    "pc": "p c",
    "gmail": "g mail",
    "disneyplus": "disney plus",
    "netflix": "net flix",
    "peacock": "pea cock",
    "notepad": "note pad",
    "folder": "fold er",
    "desktop": "desk top",
    "online": "on line",
    "shutdown": "shut down",
    "login": "log in",
}

# Unstressed words that drop out of fast speech. Never the only word, and
# never a word the label's target depends on.
DROPPABLE = {"the", "a", "an", "my", "please", "just", "up", "some", "that"}

DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

MID_FILLERS = ("uh", "um", "er")


def _words(text: str) -> List[str]:
    return text.split()


def homophone(text: str, rng: random.Random) -> Optional[Tuple[str, str]]:
    ws = _words(text)
    idx = [i for i, w in enumerate(ws) if re.sub(r"\W", "", w.lower()) in HOMOPHONES]
    if not idx:
        return None
    i = rng.choice(idx)
    bare = re.sub(r"\W", "", ws[i].lower())
    repl = HOMOPHONES[bare]
    # Preserve the original casing shape and any trailing punctuation.
    trail = ws[i][len(ws[i].rstrip(".?!,")):]
    if ws[i].isupper():
        repl = repl.upper()
    elif ws[i][:1].isupper():
        repl = repl[:1].upper() + repl[1:]
    ws[i] = repl + trail
    return " ".join(ws), f"homophone:{bare}->{HOMOPHONES[bare]}"


def split_compound(text: str, rng: random.Random) -> Optional[Tuple[str, str]]:
    ws = _words(text)
    idx = [i for i, w in enumerate(ws) if re.sub(r"\W", "", w.lower()) in SPLITS]
    if not idx:
        return None
    i = rng.choice(idx)
    bare = re.sub(r"\W", "", ws[i].lower())
    repl = SPLITS[bare]
    trail = ws[i][len(ws[i].rstrip(".?!,")):]
    if ws[i].isupper():
        repl = repl.upper()
    ws[i] = repl + trail
    return " ".join(ws), f"split:{bare}"


def drop_function_word(text: str, rng: random.Random) -> Optional[Tuple[str, str]]:
    ws = _words(text)
    # Never the final word: "how many is that" -> "how many is" is not a
    # transcription error, it is a broken sentence, and grading the model on
    # it repeats the voicify() mistake this split exists to avoid.
    idx = [i for i, w in enumerate(ws)
           if w.lower().strip(".?!,") in DROPPABLE and i != len(ws) - 1]
    if not idx or len(ws) <= 2:
        return None
    i = rng.choice(idx)
    dropped = ws[i]
    del ws[i]
    return " ".join(ws), f"dropped:{dropped.lower().strip('.?!,')}"


def spell_digits(text: str, rng: random.Random) -> Optional[Tuple[str, str]]:
    """mem_9 -> mem nine. A transcriber has no reason to emit an underscore.

    Identifiers are read out digit by digit ("mem three five six"), which is
    why that form is kept. A bare quantity is NOT: nobody transcribes 47 as
    "four seven", they write "47" or "forty seven", so multi-digit quantities
    are left alone rather than turned into a string no transcriber produces.
    """
    m = list(re.finditer(r"([a-zA-Z]+)_(\d+)", text))
    if m:
        g = rng.choice(m)
        spelled = " ".join(DIGIT_WORDS[d] for d in g.group(2))
        return (text[:g.start()] + f"{g.group(1)} {spelled}" + text[g.end():],
                "underscore_spelled")
    m2 = [g for g in re.finditer(r"\b(\d)\b", text)]
    if not m2:
        return None
    g = rng.choice(m2)
    return (text[:g.start()] + DIGIT_WORDS[g.group(1)] + text[g.end():],
            "digit_spelled")


def mid_filler(text: str, rng: random.Random) -> Optional[Tuple[str, str]]:
    """A hesitation inside the utterance, not glued to the front.

    `voicify()` only ever prefixes a filler. Real hesitation lands mid-phrase,
    which is harder because it interrupts the span the target is read from.
    """
    ws = _words(text)
    if len(ws) < 3:
        return None
    i = rng.randrange(1, len(ws))
    ws.insert(i, rng.choice(MID_FILLERS))
    return " ".join(ws), "mid_filler"


def no_punctuation(text: str, _rng: random.Random) -> Optional[Tuple[str, str]]:
    stripped = re.sub(r"[.?!,]", "", text)
    if stripped == text:
        return None
    return stripped, "punctuation_stripped"


# `spell_digits` is deliberately NOT wired in. It would only ever fire on an
# identifier like `mem_9`, whose target is lifted from the question verbatim,
# so the protected-span guard rejects every candidate it produces -- it fired
# zero times out of 1,215 rows. Leaving it in RULES would advertise a
# corruption kind that never occurs.
#
# The judgement behind the guard, rather than the mechanics: mapping "mem
# nine" back to `mem_9` is a real capability a voice assistant needs, but
# nothing in the tuned prompt states that spoken digits become an underscored
# identifier, so the model would have to guess the convention. Scoring it on
# that would measure an undocumented expectation. **Spoken-digit identifier
# transcription is therefore an untested gap, not a passed test** -- recorded
# here rather than quietly absent.
RULES = (homophone, split_compound, drop_function_word,
         mid_filler, no_punctuation)


def protected_spans(record: Dict[str, Any]) -> List[str]:
    """Substrings of the question that the label is read out of verbatim.

    A free-text target -- a filename, a folder, a search phrase, a note body
    -- is copied out of the question word for word. Corrupt inside it and the
    label stops being derivable from the input: the row is then unanswerable
    rather than hard, and marking the model wrong for it measures nothing.
    Closed-vocabulary targets like `light.bedroom` never appear verbatim, so
    this guard correctly does not fire for them.

    The gold answer's digits are protected the same way. An arithmetic row
    whose operands get mangled has a gold answer that no longer follows from
    the question, and `wrong_number` would fire on a correct response.
    """
    label = record.get("label") or {}
    call = label.get("proposed_tool_call") or {}
    question = record.get("question") or ""
    spans = []
    for key in ("target", "content"):
        val = (call.get(key) or "").strip()
        # Only guard what is actually lifted from the question.
        if len(val) >= 3 and val.lower() in question.lower():
            spans.append(val)
    return spans


def digits_protected(record: Dict[str, Any]) -> bool:
    """True when the gold answer's numbers were COMPUTED from the question's.

    The distinction matters, and getting it wrong costs real coverage in
    either direction:

    * Computed -- "divide 156 by 6" -> "26". 26 is nowhere in the question, so
      changing an operand makes the gold answer not follow from the input and
      the numeric check fires on a correct reply. Protect.
    * Copied -- "forget mem_9" -> target `mem_9`. The 9 came straight from the
      question, so spelling it out ("mem nine") is a fair and valuable test:
      a voice assistant genuinely has to map spoken digits back to an
      identifier. Protecting it here would delete that test for no reason.

    Checked as a structural invariant, not as a protected substring, because
    substring matching cannot protect a digit at all: after 6 -> "six",
    "divide 156 buy six" still contains "6" inside "156".
    """
    label = record.get("label") or {}
    spoken = label.get("spoken_answer") or ""
    question = record.get("question") or ""
    answer_nums = set(re.findall(r"\d+", spoken))
    if not answer_nums:
        return False
    question_nums = set(re.findall(r"\d+", question))
    # A number in the answer that the question never mentioned is a result.
    return bool(answer_nums - question_nums)


def _spans_intact(text: str, spans: List[str]) -> bool:
    low = text.lower()
    return all(s.lower() in low for s in spans)


def corrupt(text: str, rng: random.Random,
            spans: Optional[List[str]] = None,
            protect_digits: bool = False) -> Tuple[str, List[str]]:
    """Apply one or two rules, rejecting any that damages a protected span."""
    spans = spans or []
    original_digits = re.findall(r"\d+", text)
    rules = list(RULES)
    rng.shuffle(rules)
    kinds: List[str] = []
    out = text
    # Two corruptions is realistic for a whole utterance; more starts
    # producing strings no transcriber would emit.
    want = 1 if rng.random() < 0.55 else 2
    for rule in rules:
        if len(kinds) >= want:
            break
        res = rule(out, rng)
        if res is None:
            continue
        candidate, kind = res
        if not _spans_intact(candidate, spans):
            # This rule would have rewritten the answer along with the
            # question. Skip it and try another.
            continue
        if protect_digits and re.findall(r"\d+", candidate) != original_digits:
            # An operand changed, so the gold answer no longer follows from
            # the question and the numeric check would fire on a correct
            # reply. Reject rather than mislabel.
            continue
        out, _ = candidate, None
        kinds.append(kind)
    return out, kinds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frozen", type=Path, default=Path("data-v3/frozen"))
    ap.add_argument("--splits", default="eval_novel,eval_adversarial")
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--show", type=int, default=20)
    args = ap.parse_args()

    out_path = args.out or (args.frozen / "eval_asr.jsonl")
    rng = random.Random(args.seed)

    written = 0
    kinds: Counter = Counter()
    unchanged = 0
    samples: List[Tuple[str, str, str]] = []
    rows_out: List[Dict[str, Any]] = []

    for split in args.splits.split(","):
        split = split.strip()
        src = args.frozen / f"{split}.jsonl"
        if not src.exists():
            print(f"REFUSED: {src} missing -- run jarvis_train.freeze first")
            return 2
        for line in src.open(encoding="utf-8"):
            row = json.loads(line)
            clean = row["record"]["question"]
            spans = protected_spans(row["record"])
            noisy, applied = corrupt(
                clean, rng, spans, protect_digits=digits_protected(row["record"])
            )
            if not applied or noisy == clean:
                # No rule fired. Keeping the row would silently pad the split
                # with clean questions and flatter the score.
                unchanged += 1
                continue
            new = json.loads(json.dumps(row))  # deep copy
            new["record"]["question"] = noisy
            new["messages"] = _rewrite_user_message(new, clean, noisy)
            new["meta"]["split"] = "eval_asr"
            new["meta"]["asr_kinds"] = applied
            new["meta"]["asr_from_split"] = split
            new["meta"]["clean_row_id"] = row["meta"].get("row_id")
            new["meta"]["clean_question"] = clean
            # Re-key, rather than inheriting the clean twin's id. A noisy row
            # is a different problem, and leaving the inherited id made the
            # frozen manifest report an identical row-id hash for eval_asr and
            # eval_clean_matched -- i.e. claim the two splits were the same
            # test, which is exactly the confusion the ids exist to prevent.
            # The link to the twin lives in `clean_row_id`, where it belongs.
            new["meta"]["row_id"] = row_id(new)
            rows_out.append(new)
            for k in applied:
                kinds[k.split(":")[0]] += 1
            if len(samples) < args.show:
                samples.append((clean, noisy, ",".join(applied)))
            written += 1

    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows_out:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    digest = hashlib.sha256(
        "\n".join(r["record"]["question"] for r in rows_out).encode("utf-8")
    ).hexdigest()

    print(f"wrote {written} paired rows -> {out_path}")
    print(f"  rows where no rule applied (skipped): {unchanged}")
    print(f"  questions_sha256: {digest[:16]}")
    print(f"  seed: {args.seed}")
    print("  corruption kinds:")
    for k, n in kinds.most_common():
        print(f"    {k:<22}{n:>6}")
    print(f"\n  {args.show} samples:")
    for clean, noisy, applied in samples:
        print(f"    [{applied}]")
        print(f"      clean: {clean}")
        print(f"      noisy: {noisy}")
    return 0


def _rewrite_user_message(row: Dict[str, Any], clean: str, noisy: str) -> List[Dict[str, str]]:
    """Swap the question inside the rendered user turn.

    The user message is rendered text containing the question, so changing
    `record.question` alone would leave the model reading the clean one -- the
    split would measure nothing and look like it worked.
    """
    msgs = json.loads(json.dumps(row["messages"]))
    for m in msgs:
        if m["role"] == "user" and clean in m["content"]:
            m["content"] = m["content"].replace(clean, noisy)
            return msgs
    raise AssertionError(
        f"question not found verbatim in the user message; cannot corrupt safely: {clean!r}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
