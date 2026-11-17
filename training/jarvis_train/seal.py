"""Partition the adversarial split into a development half and a sealed half.

Why this exists. `eval_adversarial` was built to be the one measure that
widening training coverage cannot erode. That property survives exactly until
its failures start shaping the next change -- at which point it has become
development data, and the number it reports is no longer an estimate of
performance on unseen wording. Nothing about the file changes; what changes is
how many decisions have been conditioned on it.

So it is split once, now, before any run-3 result exists, by a rule that
cannot be tuned after the fact: every fourth phrasing in `template_id` sort
order is sealed. Deterministic, independent of row counts, reproducible by
inspection.

The sealed half is for one purpose: deciding whether a candidate gets
promoted. It is not scored during development, not consulted to choose
hyperparameters, and not used to pick between two candidates -- each of those
spends it. A sealed set scored twice is a development set with a longer name.

    python -m jarvis_train.seal --frozen data-v3/frozen --seal-every 4
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frozen", type=Path, default=Path("data-v3/frozen"))
    ap.add_argument("--split", default="eval_adversarial")
    ap.add_argument("--seal-every", type=int, default=4,
                    help="seal every Nth phrasing in template_id sort order")
    ap.add_argument("--force", action="store_true",
                    help="re-partition even though a sealed file already exists")
    args = ap.parse_args()

    src = args.frozen / f"{args.split}.jsonl"
    sealed_path = args.frozen / "eval_sealed.jsonl"
    dev_path = src

    if not src.exists():
        print(f"REFUSED: {src} missing -- run jarvis_train.freeze first")
        return 2
    if sealed_path.exists() and not args.force:
        print(f"REFUSED: {sealed_path} already exists.")
        print("  Re-partitioning would move phrasings between the development")
        print("  and sealed halves, which silently un-seals whatever moved.")
        return 2

    rows = [json.loads(line) for line in src.open(encoding="utf-8")]
    by_phrasing: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_phrasing[row["meta"]["template_id"]].append(row)

    phrasings = sorted(by_phrasing)
    sealed_ids = {
        tid for i, tid in enumerate(phrasings)
        if i % args.seal_every == args.seal_every - 1
    }
    dev_ids = [t for t in phrasings if t not in sealed_ids]

    dev_rows = [r for t in dev_ids for r in by_phrasing[t]]
    sealed_rows = [r for t in sorted(sealed_ids) for r in by_phrasing[t]]

    # Disjointness is the only property that matters here, so assert it rather
    # than trusting the arithmetic above.
    assert not (set(dev_ids) & sealed_ids), "dev and sealed share a phrasing"
    assert len(dev_rows) + len(sealed_rows) == len(rows), "rows lost in the split"

    for row in sealed_rows:
        row["meta"]["split"] = "eval_sealed"
        row["meta"]["sealed"] = True

    with sealed_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in sealed_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with dev_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in dev_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def digest(rs):
        return hashlib.sha256(
            "\n".join(r["record"]["question"] for r in rs).encode("utf-8")
        ).hexdigest()[:16]

    print(f"{args.split}: {len(rows)} rows over {len(phrasings)} phrasings")
    print(f"  development -> {dev_path}")
    print(f"      {len(dev_rows)} rows, {len(dev_ids)} phrasings, "
          f"questions_sha256={digest(dev_rows)}")
    print(f"  SEALED      -> {sealed_path}")
    print(f"      {len(sealed_rows)} rows, {len(sealed_ids)} phrasings, "
          f"questions_sha256={digest(sealed_rows)}")
    print(f"  rule: every {args.seal_every}th phrasing in template_id sort order")
    print("\n  The sealed file is for the promotion decision only. Scoring it")
    print("  during development spends it, and it cannot be refilled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
