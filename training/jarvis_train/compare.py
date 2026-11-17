"""Diff two evaluation reports, refusing to compare incomparable ones.

Run 2's published figures were measured on a 951-row `eval_novel` that no
longer exists: widening the template pools changed the file, so the old and
new splits are different tests. Putting those numbers in one table would be
the most natural mistake available and the hardest to notice afterwards, so
this tool checks the eval-set fingerprint first and refuses rather than
warning. A warning in a long log gets skimmed; a refusal does not.

Reports written before fingerprinting existed have no `eval_set` key at all.
Those are reported as unknown provenance and still refused, because "probably
the same split" is not a basis for a comparison that decides what gets served.

    python -m jarvis_train.compare out/report-a.json out/report-b.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# (label, numerator, denominator). Rates, so a changed row count does not by
# itself move a number.
METRICS: List[Tuple[str, str, str]] = [
    ("schema valid", "schema_valid", "total"),
    ("correct tool", "tool_exact", "want_tool"),
    ("exact target", "target_exact", "want_tool"),
    ("target: closed vocab", "target_exact_closed", "want_tool_closed"),
    ("target: free text", "target_exact_free", "want_tool_free"),
    ("correctly proposed nothing", "correct_none", "want_none"),
    ("answered correctly", "answer_ok", "want_answer"),
    ("confirmation flag ok", "confirm_match", "total"),
]

# Phrasing-weighted rates, read from the report's "macro" block. Each distinct
# phrasing gets one vote, so a family carried by one phrasing repeated sixteen
# times counts once here and sixteen times in METRICS. Shown beside the
# row-weighted figures rather than instead of them: a gain that appears only
# in the row-weighted column is a gain in how often the easy phrasings repeat.
MACRO_METRICS: List[Tuple[str, str]] = [
    ("correct tool", "tool_exact"),
    ("exact target", "target_exact"),
    ("answered correctly", "answer_ok"),
    ("correctly proposed nothing", "correct_none"),
    ("schema valid", "schema_valid"),
]

# Counts that must stay at zero; shown as absolute numbers, not rates.
ZERO_COUNTS = [
    ("schema invalid: cut off", "schema_invalid_truncated"),
    ("schema invalid: malformed", "schema_invalid_malformed"),
    ("injection obeyed", "injection_obeyed"),
    ("offered unavailable tool", "offered_unavailable"),
    ("hallucinated tool", "unknown_tool"),
    ("refused a plain question", "refused_a_plain_question"),
    ("wrong number in answer", "wrong_number"),
    ("under-action", "under_action"),
    ("over-action", "over_action"),
]


def load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def eval_identity(report: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """The (split, content digest) a report was measured on, if it recorded one."""
    info = report.get("eval_set") or {}
    digest = info.get("sha256_16")
    if not digest:
        return None
    return info.get("split", "?"), digest


def rate(counters: Dict[str, int], num: str, den: str) -> Optional[float]:
    d = counters.get(den, 0)
    if not d:
        return None
    return counters.get(num, 0) / d


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--force", action="store_true",
                        help="compare anyway; prints the mismatch on every line of output")
    parser.add_argument("--paired", action="store_true",
                        help="the two reports are a declared pairing on different "
                             "row sets (eval_clean_matched vs eval_asr), so the "
                             "fingerprint check is expected to differ")
    args = parser.parse_args()

    base, cand = load(args.baseline), load(args.candidate)
    base_id, cand_id = eval_identity(base), eval_identity(cand)

    # A paired comparison measures one model against itself on the same
    # problems worded two ways, so the row sets differ by design and the
    # fingerprint check must not refuse. It is gated on the split names being
    # the declared pair, so --paired cannot be used to wave through an
    # ordinary mismatch -- which is the whole reason the refusal exists.
    PAIRS = {("eval_clean_matched", "eval_asr")}
    if args.paired:
        names = ((base_id or ("?", ""))[0], (cand_id or ("?", ""))[0])
        if names not in PAIRS:
            print(f"REFUSED: --paired expects one of {sorted(PAIRS)}, got {names}.")
            print("  A paired comparison is between the same problems worded two")
            print("  ways. Two unrelated splits are not a pairing.")
            return 2
        print(f"\npaired comparison: {names[0]} (clean) vs {names[1]} (transcription noise)")
        print(f"baseline : {base.get('title', args.baseline.name)}")
        print(f"candidate: {cand.get('title', args.candidate.name)}")
        print(f"rows     : {base.get('examples')} vs {cand.get('examples')}  "
              "(must be equal for the pairing to hold)")
        if base.get("examples") != cand.get("examples"):
            print("  WARNING: unequal row counts -- this is no longer a paired "
                  "comparison and the delta mixes noise cost with row selection.")
        print("\nEverything below is the COST OF TRANSCRIPTION NOISE on identical")
        print("problems with identical labels, not a model-vs-model difference.")
        base_id = cand_id = ("paired", "paired")

    if base_id is None or cand_id is None:
        missing = [
            str(p) for p, r in ((args.baseline, base_id), (args.candidate, cand_id)) if r is None
        ]
        print("REFUSED: these reports do not record which eval set produced them, so "
              "there is no way to confirm they measure the same test.")
        print(f"  without a fingerprint: {', '.join(missing)}")
        print("  Re-run the evaluation to produce a fingerprinted report.")
        if not args.force:
            return 2
    elif base_id != cand_id:
        print("REFUSED: these reports were measured on different eval sets.")
        print(f"  baseline : split={base_id[0]} sha256[:16]={base_id[1]}")
        print(f"  candidate: split={cand_id[0]} sha256[:16]={cand_id[1]}")
        print("  Re-score the baseline on the candidate's eval set before comparing.")
        if not args.force:
            return 2

    warn = "  [INCOMPARABLE]" if args.force and base_id != cand_id else ""
    bc, cc = base.get("counters", {}), cand.get("counters", {})

    print(f"\nbaseline : {base.get('title', args.baseline.name)}")
    print(f"candidate: {cand.get('title', args.candidate.name)}")
    if base_id:
        print(f"eval set : {base_id[0]}  sha256[:16]={base_id[1]}")
    print(f"rows     : baseline {base.get('examples')}  candidate {cand.get('examples')}")

    # The system prompt is part of the configuration being measured, not a
    # constant. Run 2 trained under an 834-character tuned prompt and run 3
    # under a 2,382-character one, so a comparison can legitimately hold the
    # prompt fixed or deliberately vary it -- but it must never do so by
    # accident, which is what printing this prevents.
    bs = (base.get("eval_set") or {})
    cs = (cand.get("eval_set") or {})
    b_sys = (bs.get("system_sha256_16"), bs.get("system_chars"), bs.get("system_source"))
    c_sys = (cs.get("system_sha256_16"), cs.get("system_chars"), cs.get("system_source"))
    if any(b_sys) or any(c_sys):
        same = b_sys == c_sys
        print(f"prompt   : baseline {b_sys[2]} {b_sys[1]}ch sha={b_sys[0]}")
        print(f"           candidate {c_sys[2]} {c_sys[1]}ch sha={c_sys[0]}")
        if not same:
            print("           NOTE: different system prompts. Any delta below mixes the "
                  "prompt change with the weight change.")
    if bs.get("max_new_tokens") != cs.get("max_new_tokens"):
        print(f"           NOTE: different max_new_tokens "
              f"({bs.get('max_new_tokens')} vs {cs.get('max_new_tokens')}); a tighter cap "
              "can truncate a long answer into a schema failure.")

    print(f"\n{'metric':<30} {'baseline':>10} {'candidate':>10} {'delta':>9}")
    print("-" * 63)
    for label, num, den in METRICS:
        b, c = rate(bc, num, den), rate(cc, num, den)
        if b is None and c is None:
            continue
        bs = f"{100 * b:8.1f}%" if b is not None else "     n/a"
        cs = f"{100 * c:8.1f}%" if c is not None else "     n/a"
        ds = f"{100 * (c - b):+8.1f}" if (b is not None and c is not None) else "      n/a"
        print(f"{label:<30} {bs:>10} {cs:>10} {ds:>9}{warn}")

    bm = base.get("macro") or {}
    cm = cand.get("macro") or {}
    if bm or cm:
        print(f"\n{'phrasing-weighted (one vote per phrasing)':<30} "
              f"{'baseline':>10} {'candidate':>10} {'delta':>9}")
        print("-" * 63)
        for label, key in MACRO_METRICS:
            b, c = bm.get(key), cm.get(key)
            if b is None and c is None:
                continue
            bs_ = f"{100 * b:8.1f}%" if b is not None else "     n/a"
            cs_ = f"{100 * c:8.1f}%" if c is not None else "     n/a"
            ds_ = (f"{100 * (c - b):+8.1f}" if (b is not None and c is not None)
                   else "      n/a")
            print(f"{label:<30} {bs_:>10} {cs_:>10} {ds_:>9}{warn}")
        # The divergence itself is the signal worth naming.
        b_row = rate(bc, "tool_exact", "want_tool")
        b_mac = bm.get("tool_exact")
        c_row = rate(cc, "tool_exact", "want_tool")
        c_mac = cm.get("tool_exact")
        if None not in (b_row, b_mac, c_row, c_mac):
            b_gap, c_gap = (b_row - b_mac), (c_row - c_mac)
            print(f"  row-minus-phrasing gap: baseline {100*b_gap:+.1f}pts, "
                  f"candidate {100*c_gap:+.1f}pts")
            if c_gap > b_gap + 0.02:
                print("  NOTE: the candidate leans MORE on repeated phrasings than the "
                      "baseline does; its row-weighted gain overstates generalisation.")

    print(f"\n{'must stay at zero / lower is better':<30} {'baseline':>10} {'candidate':>10} {'delta':>9}")
    print("-" * 63)
    for label, key in ZERO_COUNTS:
        b, c = bc.get(key, 0), cc.get(key, 0)
        if not b and not c:
            continue
        print(f"{label:<30} {b:>10} {c:>10} {c - b:>+9}{warn}")

    # Effective sample size per family. A family carried by one phrasing can
    # swing several points on its own, so a per-family delta there is noise.
    bp = base.get("phrasings_per_family") or {}
    cp = cand.get("phrasings_per_family") or {}
    thin = sorted(f for f in set(bp) | set(cp) if min(bp.get(f, 0), cp.get(f, 0)) < 3)
    if thin:
        print(f"\nfamilies backed by fewer than 3 phrasings in one of the two runs "
              f"(per-family deltas here are noise): {', '.join(thin)}")

    bl, cl = base.get("latency_mean"), cand.get("latency_mean")
    if bl and cl:
        print(f"\nunbatched latency  baseline {bl:.2f}s  candidate {cl:.2f}s  "
              f"({cl - bl:+.2f}s, n={base.get('latency_samples', '?')}"
              f"/{cand.get('latency_samples', '?')})")
        print("  Measured on the transformers stack, not the GGUF serving path. "
              "It is not a serving-latency claim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
