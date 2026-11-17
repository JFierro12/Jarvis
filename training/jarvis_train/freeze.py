"""Freeze a reproducible experiment: materialise the eval rows and record
everything needed to repeat or refute the run.

Why materialise rather than record flags. `evaluate.py --per-phrasing 4` is
deterministic -- it keeps the first four rows of each `template_id` in file
order -- so in principle "same data dir, same flag" reproduces the same rows.
In practice that guarantee lives in two places at once (the generator's output
order and the evaluator's capping loop), and either can move without anyone
noticing that a published comparison quietly changed its test. Writing the
chosen rows to their own file collapses that to one artifact with one hash.

What the manifest is for. A comparison between two adapters is only a
comparison if everything else held still. The things that can move here are
the dataset, the system prompt, the base model revision, the chat template,
the generation budget, the LoRA hyperparameters, the sequence length, and the
code doing the grading. Each one is recorded with a full hash, so a later
disagreement about "what was run" is a lookup rather than an argument.

    python -m jarvis_train.freeze --data data-v3 --per-phrasing 4 --tag run3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from jarvis_train import contract
from jarvis_train.evaluate import production_system_prompt

HERE = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row_id(row: Dict[str, Any]) -> str:
    """Content-addressed id for one eval row.

    Keyed on everything the model is shown plus the label it is graded
    against, so the same problem keeps the same id across a regeneration and
    any change to the question, the context or the expected answer produces a
    different one. Deliberately NOT keyed on row order.
    """
    rec = row["record"]
    payload = json.dumps(
        {
            "split": row["meta"].get("split"),
            "family": row["meta"].get("family"),
            "template_id": row["meta"].get("template_id"),
            "question": rec.get("question"),
            "context": rec.get("context") or [],
            "available_tools": sorted(rec.get("available_tools") or []),
            "label": rec.get("label"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def cap_per_phrasing(rows: List[Dict[str, Any]], cap: int) -> List[Dict[str, Any]]:
    """Reproduce evaluate.py --per-phrasing exactly."""
    if not cap:
        return list(rows)
    kept: List[Dict[str, Any]] = []
    per: Dict[str, int] = defaultdict(int)
    for row in rows:
        tid = row["meta"]["template_id"]
        if per[tid] >= cap:
            continue
        per[tid] += 1
        kept.append(row)
    return kept


def git_state() -> Dict[str, Any]:
    """Commit plus a fingerprint of uncommitted work.

    Nothing here is committed, so the commit alone would describe code that is
    not the code that ran. The diff hash is what makes the record honest.
    """
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                args, cwd=str(HERE), capture_output=True, text=True, timeout=60
            ).stdout
        except Exception:  # noqa: BLE001 - a missing git must not fail a freeze
            return ""

    head = run("git", "rev-parse", "HEAD").strip()
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD").strip()
    diff = run("git", "diff", "HEAD")
    status = run("git", "status", "--porcelain")
    return {
        "head": head or None,
        "branch": branch or None,
        "working_diff_sha256": sha256_text(diff) if diff else None,
        "working_diff_bytes": len(diff.encode("utf-8")),
        "dirty_paths": sorted(
            line[3:].strip() for line in status.splitlines() if line.strip()
        ),
        "note": "nothing is committed; working_diff_sha256 identifies the code that ran",
    }


def library_versions() -> Dict[str, Any]:
    out: Dict[str, Any] = {"python": sys.version.split()[0]}
    for name in ("torch", "transformers", "peft", "bitsandbytes", "accelerate",
                 "datasets", "tokenizers"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", "unknown")
        except Exception:  # noqa: BLE001
            out[name] = None
    try:
        import torch
        out["cuda"] = torch.version.cuda
        out["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            out["gpu_total_mib"] = round(props.total_memory / 1024 / 1024)
    except Exception:  # noqa: BLE001
        pass
    return out


def tokenizer_state(model: str) -> Dict[str, Any]:
    """Chat template and special tokens, hashed.

    The chat template is part of the prompt. A transformers upgrade that
    changes Qwen's template changes every prompt in the experiment while every
    file on disk stays byte-identical, which is the hardest kind of drift to
    notice after the fact.
    """
    try:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(model)
        template = getattr(tok, "chat_template", None) or ""
        return {
            "name_or_path": model,
            "chat_template_sha256": sha256_text(template) if template else None,
            "chat_template_chars": len(template),
            "eos_token": tok.eos_token,
            "pad_token": tok.pad_token,
            "vocab_size": tok.vocab_size,
            "class": type(tok).__name__,
        }
    except Exception as exc:  # noqa: BLE001
        return {"name_or_path": model, "error": repr(exc)}


def adapter_provenance(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    info: Dict[str, Any] = {"path": str(path), "exists": True, "files": {}}
    for f in sorted(path.iterdir()):
        if f.is_file():
            info["files"][f.name] = {
                "bytes": f.stat().st_size,
                "sha256": sha256_file(f) if f.stat().st_size < 400 * 1 << 20 else None,
            }
    cfg = path / "adapter_config.json"
    if cfg.exists():
        try:
            info["adapter_config"] = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    for name in ("train_run.json", "training_args.json", "run.json"):
        p = path / name
        if p.exists():
            try:
                info["run_record"] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, default=Path("data-v3"))
    ap.add_argument("--splits", default="eval_novel,eval_adversarial")
    ap.add_argument("--per-phrasing", type=int, default=4)
    ap.add_argument("--train-file", type=Path, default=None)
    ap.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct")
    ap.add_argument("--adapters", default="out/adapter-3b-v2",
                    help="comma-separated adapter dirs to record provenance for")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="where the frozen row files go (default: <data>/frozen)")
    ap.add_argument("--also-record", default="",
                    help="comma-separated already-built eval files to hash and "
                         "record without re-materialising them (e.g. the "
                         "derived ASR split, which is produced from the frozen "
                         "rows and must not be re-capped)")
    ap.add_argument("--tag", default="run3")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing frozen set (refuses by default)")
    args = ap.parse_args()

    data = args.data
    out_dir = args.out_dir or (data / "frozen")
    train_file = args.train_file or (data / "train.jsonl")

    manifest_path = Path("out") / f"manifest-{args.tag}.json"
    if manifest_path.exists() and not args.force:
        print(f"REFUSED: {manifest_path} already exists.")
        print("  A frozen experiment is meant to be immutable -- overwriting it")
        print("  would silently redefine what every report measured against it.")
        print("  Use a new --tag, or --force if you genuinely mean to replace it.")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    splits_info: Dict[str, Any] = {}

    for split in args.splits.split(","):
        split = split.strip()
        src = data / f"{split}.jsonl"
        if not src.exists():
            print(f"REFUSED: {src} does not exist")
            return 2
        rows = [json.loads(line) for line in src.open(encoding="utf-8")]
        kept = cap_per_phrasing(rows, args.per_phrasing)

        ids = [row_id(r) for r in kept]
        if len(set(ids)) != len(ids):
            dupes = len(ids) - len(set(ids))
            print(f"  note: {split} has {dupes} rows that are byte-identical "
                  f"problems (same question, context and label)")

        dst = out_dir / f"{split}.jsonl"
        with dst.open("w", encoding="utf-8", newline="\n") as fh:
            for rid, row in zip(ids, kept, strict=True):
                row["meta"]["row_id"] = rid
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        phrasings = sorted({r["meta"]["template_id"] for r in kept})
        families: Dict[str, int] = defaultdict(int)
        for r in kept:
            families[r["meta"]["family"]] += 1

        splits_info[split] = {
            "source_file": str(src),
            "source_sha256": sha256_file(src),
            "source_rows": len(rows),
            "frozen_file": str(dst),
            "frozen_sha256": sha256_file(dst),
            "frozen_rows": len(kept),
            "per_phrasing_cap": args.per_phrasing or None,
            "distinct_phrasings": len(phrasings),
            # Hash of the ordered row ids: the identity of the test itself,
            # independent of how the file happens to be formatted.
            "row_ids_sha256": sha256_text("\n".join(ids)),
            # The same digest evaluate.py computes, so a report can be matched
            # to this manifest without re-reading the rows.
            "questions_sha256_16": sha256_text(
                "\n".join(r["record"]["question"] for r in kept)
            )[:16],
            "rows_per_family": dict(sorted(families.items())),
            "row_ids": ids,
        }
        print(f"  {split}: {len(rows)} -> {len(kept)} rows, "
              f"{len(phrasings)} phrasings -> {dst}")

    # Files that already exist in their final form. Recorded, not rebuilt:
    # the ASR split is derived from the frozen rows above, so re-capping it
    # would change it, and materialising it here would make this command the
    # second place that defines it.
    for spec in filter(None, (s.strip() for s in args.also_record.split(","))):
        path = Path(spec)
        if not path.exists():
            print(f"REFUSED: --also-record {path} does not exist")
            return 2
        rows = [json.loads(line) for line in path.open(encoding="utf-8")]
        name = path.stem
        ids = [r["meta"].get("row_id") or row_id(r) for r in rows]
        fams: Dict[str, int] = defaultdict(int)
        for r in rows:
            fams[r["meta"]["family"]] += 1
        splits_info[name] = {
            "frozen_file": str(path),
            "frozen_sha256": sha256_file(path),
            "frozen_rows": len(rows),
            "derived": True,
            "derived_from": sorted({r["meta"].get("asr_from_split")
                                    for r in rows if r["meta"].get("asr_from_split")})
            or None,
            "distinct_phrasings": len({r["meta"]["template_id"] for r in rows}),
            "row_ids_sha256": sha256_text("\n".join(ids)),
            "questions_sha256_16": sha256_text(
                "\n".join(r["record"]["question"] for r in rows)
            )[:16],
            "rows_per_family": dict(sorted(fams.items())),
            "row_ids": ids,
        }
        print(f"  recorded {name}: {len(rows)} rows (derived) -> {path}")

    prod = production_system_prompt()
    tuned = contract.TUNED_SYSTEM_PROMPT
    run2_prompt_path = Path("out/run2-tuned-prompt.txt")

    prompts: Dict[str, Any] = {
        "tuned_current": {
            "chars": len(tuned), "sha256": sha256_text(tuned),
            "source": "contract.TUNED_SYSTEM_PROMPT",
        },
        "production_current": {
            "chars": len(prod), "sha256": sha256_text(prod),
            "source": "backend anthropic_reasoning.SYSTEM_PROMPT",
        },
    }
    if run2_prompt_path.exists():
        run2 = run2_prompt_path.read_text(encoding="utf-8")
        # Verified against the dataset, not reconstructed: run 2's prompt
        # survives only as the system message embedded in its own rows.
        verified = None
        run2_train = Path("data/train.jsonl")
        if run2_train.exists():
            systems = set()
            for line in run2_train.open(encoding="utf-8"):
                systems.add(json.loads(line)["messages"][0]["content"])
            verified = (len(systems) == 1 and run2 in systems)
        prompts["tuned_run2_recovered"] = {
            "chars": len(run2), "sha256": sha256_text(run2),
            "source": str(run2_prompt_path),
            "verified_against_run2_dataset": verified,
            "distinct_system_prompts_in_run2_train": len(systems) if verified is not None else None,
        }

    manifest = {
        "tag": args.tag,
        "created_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "splits": splits_info,
        "train": {
            "file": str(train_file),
            "sha256": sha256_file(train_file) if train_file.exists() else None,
            "rows": sum(1 for _ in train_file.open(encoding="utf-8"))
            if train_file.exists() else None,
        },
        "prompts": prompts,
        "base_model": tokenizer_state(args.model),
        "adapters": {
            a.strip(): adapter_provenance(Path(a.strip()))
            for a in args.adapters.split(",") if a.strip()
        },
        "libraries": library_versions(),
        "git": git_state(),
        "generation": {
            "note": "recorded per report; the manifest fixes the rows, not the decode",
            "decode": "greedy (do_sample=False)",
        },
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nmanifest -> {manifest_path}")
    for split, info in splits_info.items():
        print(f"  {split}: rows={info['frozen_rows']} "
              f"phrasings={info['distinct_phrasings']} "
              f"row_ids_sha256={info['row_ids_sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
