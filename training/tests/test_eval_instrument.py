"""Checks on the measuring instrument rather than on the model.

Run 2's headline figure was wrong about what it measured, not arithmetically
wrong: it averaged over rows when the rows were repetitions of ~60 phrasings,
and it counted a cut-off reply as a malformed one. Both are defects in the
evaluator, and neither would ever fail a test of the model. So they get tests
of their own here.

Stdlib-only, like test_dataset_contract.py: nothing here may import torch or
transformers, so it runs under any interpreter in the repo.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis_train.asr_eval import (  # noqa: E402
    DIGIT_WORDS,
    corrupt,
    drop_function_word,
    protected_spans,
)
from jarvis_train.evaluate import (  # noqa: E402
    _NUMBER,
    Scores,
    _incomplete_reason,
    _states_one_of,
)


def credits(gold: str, said: str) -> bool:
    return _states_one_of(said, _NUMBER.findall(gold))


# ------------------------------------------------------- numeric answer check

@pytest.mark.parametrize(
    "gold,said,expected,why",
    [
        ("1,440, sir.", "24 hours, sir - 1440 minutes.", True,
         "thousands separator: the measured false negative"),
        ("1,440, sir.", "1,440 minutes, sir.", True, "exact, with comma"),
        ("27 dollars, sir - you save 3.", "Twenty-seven dollars, sir.", True,
         "prose numeral is still a correct answer"),
        ("64, sir.", "sixty-four, sir.", True, "hyphenated prose"),
        ("1440", "one thousand four hundred forty", True, "compound prose"),
        ("5:25, sir.", "5:25, sir, leave then.", True, "clock time stated"),
        ("27 dollars, sir.", "Twenty-nine dollars and seven cents.", False,
         "genuinely wrong answer"),
        ("5:25, sir.", "7:00, sir.", False, "wrong clock time"),
        ("27 dollars", "That will be 127 dollars", False,
         "substring collision must not be credited"),
        ("3 dollars", "2.75 dollars", False,
         "decimal substring must not be credited"),
    ],
)
def test_numeric_answer_check_in_both_directions(gold, said, expected, why):
    """The grader marked correct arithmetic wrong, and wrong arithmetic right.

    `"1,440" in "1440 minutes"` is False, so a correct reply scored
    wrong_number -- which alone made the pre-registered "zero wrong_number"
    bar unreachable for reasons unrelated to the model. The same substring
    test credited gold "27" to a reply saying "127", inflating the score. A
    check that can fail in both directions needs cases in both directions.
    """
    assert credits(gold, said) is expected, why


def test_unrecognised_number_words_fail_closed():
    """The prose reader is a recall aid, not a parser.

    Anything it cannot read is simply not credited. That is the safe
    direction: an unparsed number understates the model, where a loose reader
    would silently pass wrong answers.
    """
    assert credits("27 dollars", "a score and seven dollars") is False

FROZEN = ROOT / "data-v3" / "frozen"
MANIFEST = ROOT / "out" / "manifest-run3.json"


# ---------------------------------------------------------------- truncation

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "empty"),
        ("   ", "empty"),
        ('{"spoken_answer": "Opening Goo', "truncated"),
        ('{"a": {"b": 1}', "truncated"),
        ('{"spoken_answer": "he said \\"hi', "truncated"),
        ('{"spoken_answer": "hi"}', "malformed"),
        ("Sure thing, sir.", "malformed"),
        ('{"a": 1} and some trailing chatter', "malformed"),
    ],
)
def test_truncation_is_distinguished_from_malformedness(raw, expected):
    """A cut-off reply and a badly-formatted one need opposite responses.

    Truncation means --max-new-tokens was too low; malformedness means the
    model cannot hold the format. Collapsing them into one `schema_invalid`
    counter sends you to retrain when you should have raised a budget.
    """
    assert _incomplete_reason(raw) == expected


def test_trailing_chatter_is_not_called_truncation():
    """The specific false positive worth guarding.

    A complete object followed by more prose is a model that would not stop,
    not a reply that was cut off. Calling it truncation would point the fix at
    the generation budget, which was never the problem.
    """
    assert _incomplete_reason('{"spoken_answer": "Done."} Anything else?') == "malformed"


# ------------------------------------------------------------ macro averages

def test_macro_weights_each_phrasing_equally():
    """The defect that made run 2's 93.2% unreadable, as a unit test.

    Four rows of an easy phrasing, all correct; one row of a hard phrasing,
    wrong. Per row that is 80%. Per phrasing it is 50%. Both are honest
    answers to different questions, and reporting only the first is what let
    24 families scored on one phrasing read as a precise result.
    """
    s = Scores()
    for _ in range(4):
        s._cur_phrasing = "easy"
        s.add("fam", "want_tool")
        s.add("fam", "tool_exact")
    s._cur_phrasing = "hard"
    s.add("fam", "want_tool")

    assert s.rate("tool_exact", "want_tool") == pytest.approx(0.8)
    assert s.macro("tool_exact", "want_tool") == pytest.approx(0.5)


def test_macro_and_row_rates_agree_when_repetition_is_even():
    """Sanity bound: the two averages may only diverge through repetition."""
    s = Scores()
    for tid in ("a", "b", "c", "d"):
        s._cur_phrasing = tid
        s.add("fam", "want_tool")
        if tid != "d":
            s.add("fam", "tool_exact")
    assert s.rate("tool_exact", "want_tool") == pytest.approx(0.75)
    assert s.macro("tool_exact", "want_tool") == pytest.approx(0.75)


def test_macro_is_none_without_a_denominator():
    assert Scores().macro("tool_exact", "want_tool") is None


# ------------------------------------------------------- the frozen eval set

@pytest.mark.skipif(not MANIFEST.exists(), reason="experiment not frozen yet")
def test_frozen_rows_still_match_the_manifest():
    """A frozen experiment that silently changed is worse than none.

    Every report measured against this manifest cites its row-id hash. If the
    files move and the manifest does not, two incomparable runs will compare
    cleanly, which is the exact failure the fingerprinting exists to prevent.
    """
    import hashlib

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for split, info in manifest["splits"].items():
        path = ROOT / info["frozen_file"]
        assert path.exists(), f"{split}: frozen file is gone: {path}"
        rows = [json.loads(line) for line in path.open(encoding="utf-8")]
        assert len(rows) == info["frozen_rows"]
        ids = [r["meta"]["row_id"] for r in rows]
        digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
        assert digest == info["row_ids_sha256"], (
            f"{split}: the frozen rows no longer hash to the manifest value. "
            "Re-freeze under a NEW tag rather than overwriting, and record "
            "which published comparisons that invalidates."
        )


@pytest.mark.skipif(not MANIFEST.exists(), reason="experiment not frozen yet")
def test_run2_prompt_was_verified_against_its_own_dataset():
    """Run 2's prompt is recovered evidence, not a remembered string.

    It was never committed; it survives only as the system message embedded in
    run 2's training rows. The manifest must record that it was checked
    against that file, because an approximation here would silently
    mis-attribute run 3's gain between the prompt and the weights.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rec = manifest["prompts"].get("tuned_run2_recovered")
    if rec is None:
        pytest.skip("run 2 prompt artifact not present")
    assert rec["verified_against_run2_dataset"] is True
    assert rec["distinct_system_prompts_in_run2_train"] == 1


# --------------------------------------------------------- the ASR noise set

@pytest.mark.skipif(not (FROZEN / "eval_asr.jsonl").exists(),
                    reason="asr split not generated")
class TestAsrSplit:
    @staticmethod
    def rows():
        return [json.loads(line)
                for line in (FROZEN / "eval_asr.jsonl").open(encoding="utf-8")]

    def test_every_question_actually_changed(self):
        """A row that no rule touched would pad the split with clean input."""
        same = [r["record"]["question"] for r in self.rows()
                if r["record"]["question"] == r["meta"]["clean_question"]]
        assert not same, f"{len(same)} rows are identical to their clean twin"

    def test_the_model_is_shown_the_noisy_question(self):
        """Rewriting record.question alone would measure nothing.

        The prompt is built from the rendered user message, so if the
        corruption does not reach it the split scores the clean question while
        appearing to test noise -- a passing result that means nothing.
        """
        for r in self.rows():
            user = [m["content"] for m in r["messages"] if m["role"] == "user"][0]
            assert r["record"]["question"] in user

    def test_protected_spans_survive_corruption(self):
        """The label has to stay derivable from the corrupted question.

        A free-text target is copied verbatim out of the question. Corrupt
        inside it and the row becomes unanswerable rather than hard, so a
        wrong answer there measures the generator, not the model.
        """
        damaged = []
        for r in self.rows():
            clean_rec = dict(r["record"])
            clean_rec["question"] = r["meta"]["clean_question"]
            noisy = r["record"]["question"].lower()
            for span in protected_spans(clean_rec):
                if span.lower() not in noisy:
                    damaged.append((span, r["meta"]["clean_question"],
                                    r["record"]["question"]))
        assert not damaged, f"corruption rewrote the answer along with the question: {damaged[:5]}"

    def test_noisy_rows_do_not_reuse_their_clean_twin_s_row_id(self):
        """A noisy row is a different problem and needs a different id.

        Deep-copying the clean row carried its `row_id` across, which made the
        frozen manifest report an identical row-id hash for `eval_asr` and
        `eval_clean_matched` -- i.e. assert that the clean and noisy splits
        were the same test. The link to the twin belongs in `clean_row_id`,
        which is where it now lives.
        """
        rows = self.rows()
        own = {r["meta"]["row_id"] for r in rows}
        twins = {r["meta"]["clean_row_id"] for r in rows}
        assert len(own) == len(rows), "row_ids are not unique within eval_asr"
        assert not (own & twins), (
            f"{len(own & twins)} noisy rows still carry their clean twin's row_id"
        )
        matched = FROZEN / "eval_clean_matched.jsonl"
        if matched.exists():
            clean = {json.loads(line)["meta"]["row_id"]
                     for line in matched.open(encoding="utf-8")}
            assert not (own & clean), "eval_asr shares row_ids with eval_clean_matched"

    def test_labels_are_identical_to_the_clean_twin(self):
        """Paired design: only the wording may differ, never the answer."""
        clean_by_id = {}
        for split in ("eval_novel", "eval_adversarial"):
            p = FROZEN / f"{split}.jsonl"
            if not p.exists():
                continue
            for line in p.open(encoding="utf-8"):
                row = json.loads(line)
                clean_by_id[row["meta"]["row_id"]] = row["record"]["label"]
        checked = 0
        for r in self.rows():
            twin = clean_by_id.get(r["meta"].get("clean_row_id"))
            if twin is None:
                continue
            assert r["record"]["label"] == twin, (
                f"ASR row's label drifted from its clean twin: "
                f"{r['meta']['clean_question']!r}"
            )
            checked += 1
        assert checked > 100, f"only {checked} pairs checked; the pairing is broken"

    def test_no_multi_digit_quantity_is_spelled_out(self):
        """Guard on a corruption that was generated and then removed.

        An earlier version turned "47" into "four seven". No transcriber emits
        that -- it writes "47" or "forty seven" -- so it graded the model on
        input no user could produce.

        Asserted at the source rather than by scanning rows. Scanning cannot
        work: the homophone rule legitimately turns "closes at six for me"
        into "six four me", which is one of the commonest real transcription
        errors and leaves the label intact, yet is indistinguishable by
        pattern from a spelled-out quantity. So the invariant is checked where
        it is actually decided -- in the rule itself.
        """
        from jarvis_train import asr_eval
        assert asr_eval.spell_digits not in asr_eval.RULES, (
            "spell_digits is wired in again; it fires only on identifiers "
            "whose target is lifted verbatim, so re-enabling it needs the "
            "protected-span decision revisited (see its comment in RULES)"
        )

    def test_spell_digits_never_emits_a_digit_by_digit_quantity(self):
        """The removed artifact, pinned directly on the rule.

        If the rule is ever re-enabled, it must still refuse to render 47 as
        "four seven". Identifier digits (`mem_9` -> "mem nine") stay allowed.
        """
        import random

        from jarvis_train.asr_eval import spell_digits
        singles = set(DIGIT_WORDS.values())
        for text in ("divide 156 by 6", "11 packs of 47 how many is that",
                     "set it for 2025", "what is 338 minus 19"):
            for seed in range(30):
                res = spell_digits(text, random.Random(seed))
                if res is None:
                    continue
                out, _kind = res
                toks = [w.strip(".?!,").lower() for w in out.split()]
                pairs = [(toks[i], toks[i + 1]) for i in range(len(toks) - 1)]
                assert not [p for p in pairs if p[0] in singles and p[1] in singles], (
                    f"{text!r} -> {out!r} spells a multi-digit quantity digit by digit"
                )

    def test_arithmetic_operands_are_never_corrupted(self):
        """A mangled operand makes the gold answer not follow from the question.

        `wrong_number` compares the reply's digits against the gold answer's.
        Change the operands and that check fires on a correct response.
        """
        bad = []
        for r in self.rows():
            label = r["record"]["label"]
            spoken = (label.get("spoken_answer") or "")
            if not re.search(r"\d", spoken):
                continue
            clean_nums = re.findall(r"\d+", r["meta"]["clean_question"])
            noisy_nums = re.findall(r"\d+", r["record"]["question"])
            if clean_nums != noisy_nums:
                bad.append((r["meta"]["clean_question"], r["record"]["question"]))
        assert not bad, f"operands changed on a row with a numeric gold answer: {bad[:5]}"


# ---------------------------------------------------------- the sealed split

SEALED = FROZEN / "eval_sealed.jsonl"


@pytest.mark.skipif(not SEALED.exists(), reason="no sealed split")
class TestSealedSplit:
    @staticmethod
    def ids(path: Path):
        return {json.loads(line)["meta"]["template_id"]
                for line in path.open(encoding="utf-8")}

    @staticmethod
    def questions(path: Path, key="question"):
        out = set()
        for line in path.open(encoding="utf-8"):
            row = json.loads(line)
            out.add(row["record"]["question"] if key == "question"
                    else row["meta"].get("clean_question"))
        return out

    def test_sealed_and_development_phrasings_are_disjoint(self):
        """A phrasing in both halves is not sealed, whatever the file says."""
        dev = self.ids(FROZEN / "eval_adversarial.jsonl")
        sealed = self.ids(SEALED)
        assert not (dev & sealed), f"phrasings in both halves: {sorted(dev & sealed)}"

    def test_sealed_phrasings_do_not_leak_through_the_asr_split(self):
        """The derived split is the back door worth checking.

        `eval_asr` is built from the frozen eval files. Build it from the
        adversarial split before sealing and every sealed phrasing is exposed
        in corrupted form, which spends the sealed set without anyone
        scoring it directly.
        """
        asr = FROZEN / "eval_asr.jsonl"
        if not asr.exists():
            pytest.skip("asr split not generated")
        sealed_q = self.questions(SEALED)
        asr_clean = self.questions(asr, key="clean_question")
        assert not (sealed_q & asr_clean), (
            f"{len(sealed_q & asr_clean)} sealed questions appear in the ASR split"
        )
        assert not (self.ids(SEALED) & self.ids(asr))

    def test_sealed_split_is_never_the_default_anywhere(self):
        """It must take an explicit flag to score it.

        A sealed set that some script reaches by default will be scored by
        accident, and a sealed set scored twice is a development set with a
        longer name.
        """
        for mod in ("evaluate.py", "compare.py", "asr_eval.py", "freeze.py"):
            src = (ROOT / "jarvis_train" / mod).read_text(encoding="utf-8")
            for line in src.splitlines():
                if "eval_sealed" in line and "default" in line:
                    raise AssertionError(
                        f"{mod} names eval_sealed near a default: {line.strip()}"
                    )


# ------------------------------------------------------- the comparison tool

def _report(tmp_path: Path, name: str, split: str, digest: str, **counters) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({
        "title": name,
        "examples": counters.pop("examples", 10),
        "eval_set": {"split": split, "sha256_16": digest},
        "counters": {"want_tool": 10, "tool_exact": 8, **counters},
        "macro": {"tool_exact": 0.5},
    }), encoding="utf-8")
    return p


def _run_compare(*args: str):
    import subprocess
    return subprocess.run(
        [sys.executable, "-m", "jarvis_train.compare", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )


def test_compare_refuses_reports_from_different_eval_sets(tmp_path):
    """The guard that protects every number in RESULTS.md.

    Run 2's published figures were measured on a 951-row eval_novel that no
    longer exists. Putting those beside a new number in one table is the most
    natural mistake available and the hardest to spot afterwards, so this
    refuses rather than warning -- a warning in a long log gets skimmed.
    """
    a = _report(tmp_path, "a.json", "eval_novel", "aaaaaaaaaaaaaaaa")
    b = _report(tmp_path, "b.json", "eval_novel", "bbbbbbbbbbbbbbbb")
    res = _run_compare(str(a), str(b))
    assert res.returncode == 2, res.stdout
    assert "REFUSED" in res.stdout


def test_compare_refuses_reports_with_no_fingerprint(tmp_path):
    """"Probably the same split" is not a basis for a promotion decision."""
    a = tmp_path / "old.json"
    a.write_text(json.dumps({"counters": {}, "examples": 5}), encoding="utf-8")
    b = _report(tmp_path, "new.json", "eval_novel", "cccccccccccccccc")
    res = _run_compare(str(a), str(b))
    assert res.returncode == 2
    assert "REFUSED" in res.stdout


def test_compare_accepts_matching_eval_sets(tmp_path):
    """Non-vacuousness: the refusals above must not be refusing everything."""
    d = "dddddddddddddddd"
    a = _report(tmp_path, "a.json", "eval_novel", d)
    b = _report(tmp_path, "b.json", "eval_novel", d, tool_exact=9)
    res = _run_compare(str(a), str(b))
    assert res.returncode == 0, res.stdout
    assert "correct tool" in res.stdout


def test_paired_mode_cannot_wave_through_an_ordinary_mismatch(tmp_path):
    """--paired must not become a general-purpose override.

    It exists for one declared pairing -- the same problems worded cleanly and
    noisily. If it accepted any two splits it would be --force with a friendlier
    name, and the fingerprint refusal would be decorative.
    """
    a = _report(tmp_path, "a.json", "eval_novel", "aaaaaaaaaaaaaaaa")
    b = _report(tmp_path, "b.json", "eval_adversarial", "bbbbbbbbbbbbbbbb")
    res = _run_compare(str(a), str(b), "--paired")
    assert res.returncode == 2, res.stdout
    assert "paired" in res.stdout.lower()


def test_paired_mode_accepts_the_declared_pairing(tmp_path):
    a = _report(tmp_path, "clean.json", "eval_clean_matched", "aaaaaaaaaaaaaaaa")
    b = _report(tmp_path, "noisy.json", "eval_asr", "bbbbbbbbbbbbbbbb")
    res = _run_compare(str(a), str(b), "--paired")
    assert res.returncode == 0, res.stdout
    assert "TRANSCRIPTION NOISE" in res.stdout


# ------------------------------------------------- rules, independent of data

def test_drop_rule_never_removes_the_final_word():
    """"how many is that" -> "how many is" is a broken sentence, not an error.

    This is the voicify() failure in miniature: a transform that produces
    something no speaker said, which the model is then marked wrong for
    mishandling.
    """
    import random
    for text in ("how many is that", "take a screenshot please",
                 "turn on the light please", "what is that"):
        for seed in range(40):
            res = drop_function_word(text, random.Random(seed))
            if res is None:
                continue
            out, _ = res
            assert out.split()[-1] == text.split()[-1], (
                f"dropped the final word of {text!r} -> {out!r}"
            )


def test_corrupt_rejects_rules_that_damage_a_protected_span():
    """The guard must actually fire, not merely exist.

    'close' -> 'clothes' is a legitimate homophone, but when the target IS
    the word 'close' the substitution destroys the answer, so it has to be
    rejected rather than applied.
    """
    import random
    question = "write to the file close notes.md"
    spans = ["close notes.md"]
    for seed in range(60):
        out, kinds = corrupt(question, random.Random(seed), spans)
        assert "close notes.md" in out.lower(), (
            f"seed {seed}: protected span destroyed by {kinds}: {out!r}"
        )
