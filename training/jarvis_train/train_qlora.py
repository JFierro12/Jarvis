"""QLoRA fine-tune of a local model on the JARVIS contract, sized for 8 GB.

Why a hand-written loop instead of Trainer/SFTTrainer: this environment has
transformers 5.x, where ``Trainer`` argument names and TRL's ``SFTTrainer``
signature have both moved. A plain loop has no version surface, and it makes the
two things that actually decide whether this works explicit and auditable:

1. **Completion-only loss.** Prompt tokens are masked to -100 so the model is
   never scored for reproducing the system prompt or the user's words. Training
   on the full sequence is the usual reason a tool-calling fine-tune learns to
   parrot the input and still emits malformed JSON.
2. **Peak VRAM is measured, not assumed.** On an 8 GB laptop card shared with
   the Windows compositor, the useful number is the one the card reports, so
   it is logged every few steps and summarised at the end.

Defaults target Qwen2.5-3B-Instruct: Apache-2.0 and ungated (no HF token
needed), and for a narrow structured-extraction task a tuned 3B is both more
reliable and ~2x faster at inference than the 8B it replaces - and inference
latency is what a voice assistant is judged on.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time

# Batches are padded to the longest example in the batch, so shapes vary from
# step to step and the caching allocator ends up holding a separate cached
# block per shape. Measured on this card: peak *allocated* was 4.84 GiB but
# *reserved* grew to 9.19 GiB -- past the 8 GiB physical, so Windows backs the
# excess with host RAM. Expandable segments let the allocator grow one segment
# instead of caching many fixed-size blocks, which keeps reserved near
# allocated. Set before torch is imported, and only if the caller has not
# already chosen a policy.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import torch
from torch.utils.data import DataLoader, Dataset

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OUT_DIR = Path(__file__).resolve().parents[1] / "out"

IGNORE_INDEX = -100


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


@dataclass
class Example:
    input_ids: List[int]
    labels: List[int]


class JarvisDataset(Dataset):
    """Chat-templated examples with the prompt masked out of the loss."""

    def __init__(self, path: Path, tokenizer, max_len: int, limit: int = 0):
        self.examples: List[Example] = []
        self.dropped = 0
        self.lengths: List[int] = []

        rows = [json.loads(line) for line in path.open(encoding="utf-8")]
        if limit:
            rows = rows[:limit]

        for row in rows:
            messages = row["messages"]
            prompt_text = tokenizer.apply_chat_template(
                messages[:-1], tokenize=False, add_generation_prompt=True
            )
            full_text = prompt_text + messages[-1]["content"] + (tokenizer.eos_token or "")

            prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
            full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]

            self.lengths.append(len(full_ids))
            if len(full_ids) > max_len:
                # Truncating the answer would teach truncated JSON, which is
                # worse than not training on the example at all.
                self.dropped += 1
                continue

            labels = list(full_ids)
            for i in range(min(len(prompt_ids), len(labels))):
                labels[i] = IGNORE_INDEX
            self.examples.append(Example(full_ids, labels))

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> Example:
        return self.examples[index]


def collate(batch: List[Example], pad_id: int) -> Dict[str, torch.Tensor]:
    width = max(len(item.input_ids) for item in batch)
    input_ids, labels, attention = [], [], []
    for item in batch:
        pad = width - len(item.input_ids)
        input_ids.append(item.input_ids + [pad_id] * pad)
        labels.append(item.labels + [IGNORE_INDEX] * pad)
        attention.append([1] * len(item.input_ids) + [0] * pad)
    return {
        "input_ids": torch.tensor(input_ids, dtype=torch.long),
        "labels": torch.tensor(labels, dtype=torch.long),
        "attention_mask": torch.tensor(attention, dtype=torch.long),
    }


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


def build_model(model_name: str, lora_r: int, lora_alpha: int, dropout: float, four_bit: bool,
                grad_checkpoint: bool = True):
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    kwargs: Dict[str, Any] = {"dtype": torch.bfloat16, "device_map": {"": 0}}
    if four_bit:
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            # Double quantisation saves roughly another 0.4 GB on a 7B, which
            # is the difference between fitting and not on this card.
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(model_name, **kwargs)
    model.config.use_cache = False

    if four_bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=grad_checkpoint)
    # Checkpointing recomputes activations in the backward pass, buying memory
    # with time. Keep it ON here. Measured on this card, batch 2 / seq 384:
    #   checkpointing ON   peak  4.59 GiB   (fits, no paging)
    #   checkpointing OFF  peak 11.53 GiB   (pages to host RAM; ETA 11.8h)
    # Turning it off looked free because the weights are small, but 36 layers
    # of retained activations are not. The ~30% per-step saving is worthless if
    # the run no longer fits in VRAM.
    if grad_checkpoint:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()

    config = LoraConfig(
        r=lora_r,
        lora_alpha=lora_alpha,
        lora_dropout=dropout,
        bias="none",
        task_type="CAUSAL_LM",
        # Attention *and* MLP. Attention-only adapters are cheaper but learn
        # formatting far less reliably on small models.
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, config)
    return model


def vram() -> str:
    if not torch.cuda.is_available():
        return "n/a"
    alloc = torch.cuda.memory_allocated() / 1024**3
    peak = torch.cuda.max_memory_allocated() / 1024**3
    reserved = torch.cuda.memory_reserved() / 1024**3
    return f"alloc {alloc:.2f} / reserved {reserved:.2f} / peak {peak:.2f} GiB"


# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="QLoRA fine-tune for the JARVIS contract.")
    parser.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct")
    parser.add_argument("--train", type=Path, default=DATA_DIR / "train.jsonl")
    parser.add_argument("--out", type=Path, default=OUT_DIR / "adapter")
    # One epoch, measured rather than assumed. On the run-1 dataset (5,743 rows,
    # 2 epochs, 716 steps, 122 minutes) training loss reached 0.034 by step 200
    # and was flat from step 300 (0.0235) to step 710 (0.0192). The second epoch
    # changed nothing and cost 61 minutes. Templated data converges fast; raise
    # this only if the loss is still falling at the end of a run.
    parser.add_argument("--epochs", type=float, default=1.0)
    # Batch size is capped by the *logits*, not the weights. Qwen2.5's vocab is
    # 151,936, so one forward pass materialises
    # batch x seq x 151936 floats for the LM head, upcast to fp32 for the
    # cross-entropy. At batch 8 / seq 384 that single tensor is ~1.9 GiB, and
    # measured peak reserved hit 9.94 GiB on an 8 GiB card -- Windows WDDM
    # silently pages the overflow into system RAM instead of failing, which
    # turned a ~40 minute run into a 5.5 hour one. Batch 2 keeps that tensor
    # near 470 MiB. Raise grad_accum, not batch size.
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--max-len", type=int, default=384)
    parser.add_argument("--lora-r", type=int, default=32)
    parser.add_argument("--lora-alpha", type=int, default=64)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=20261110)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-4bit", action="store_true")
    parser.add_argument("--no-grad-checkpoint", action="store_true",
                        help="trade VRAM for ~30%% less time; safe at batch 2 on an 8 GiB card")
    parser.add_argument("--log-every", type=int, default=10)
    parser.add_argument("--save-every", type=int, default=0, help="optimizer steps; 0 = only at end")
    parser.add_argument("--vram-fraction", type=float, default=0.0,
                        help="hard cap on the allocator as a fraction of total VRAM "
                             "(e.g. 0.82); 0 = uncapped. Turns a silent spill into an OOM.")
    parser.add_argument("--keep-checkpoints", type=int, default=3,
                        help="how many step checkpoints to retain (each ~0.5GB); 0 = keep all")
    parser.add_argument("--resume-from", default="",
                        help="path to a checkpoint-NNNNN directory to resume from")
    args = parser.parse_args()

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    from transformers import AutoTokenizer

    print(f"loading tokenizer: {args.model}")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print("tokenizing dataset")
    dataset = JarvisDataset(args.train, tokenizer, args.max_len, args.limit)
    lengths = sorted(dataset.lengths)
    print(f"  {len(dataset)} usable examples ({dataset.dropped} over max_len={args.max_len}, dropped)")
    if lengths:
        print(f"  token length  p50 {lengths[len(lengths)//2]}  "
              f"p95 {lengths[int(len(lengths)*0.95)]}  max {lengths[-1]}")

    if not len(dataset):
        raise SystemExit("no usable training examples")

    loader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=lambda b: collate(b, tokenizer.pad_token_id),
        drop_last=True,
    )

    print(f"loading model in {'4-bit NF4' if not args.no_4bit else 'bf16'}")
    model = build_model(args.model, args.lora_r, args.lora_alpha, args.lora_dropout,
                        not args.no_4bit, not args.no_grad_checkpoint)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  trainable {trainable/1e6:.1f}M / {total/1e6:.0f}M  ({100*trainable/total:.2f}%)")
    print(f"  after load: {vram()}")

    steps_per_epoch = max(1, len(loader) // args.grad_accum)
    total_steps = max(1, int(steps_per_epoch * args.epochs))
    warmup = max(1, int(total_steps * args.warmup_ratio))
    print(f"  {len(loader)} batches/epoch -> {steps_per_epoch} optimizer steps/epoch, "
          f"{total_steps} total, {warmup} warmup")

    try:
        from bitsandbytes.optim import AdamW8bit

        optimizer = AdamW8bit(
            [p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0
        )
        print("  optimizer: bitsandbytes AdamW8bit")
    except Exception as exc:  # pragma: no cover - fallback path
        optimizer = torch.optim.AdamW(
            [p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0
        )
        print(f"  optimizer: torch AdamW (8-bit unavailable: {exc})")

    def lr_at(step: int) -> float:
        if step < warmup:
            return args.lr * (step + 1) / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        return args.lr * 0.5 * (1.0 + math.cos(math.pi * min(1.0, progress)))

    # A hard ceiling on the allocator, well under physical VRAM.
    #
    # Twice tonight this run has died the same way: the allocator reserved
    # more than the card physically has (9.59 GiB, then 8.07 GiB on an 8.19
    # GiB card), Windows WDDM silently backed the excess with host RAM over
    # PCIe, and training slowed 51x while nvidia-smi still read 100%
    # utilisation. There is no error, no OOM and no throttle flag -- the only
    # visible symptom is power draw collapsing to a third of the cap.
    #
    # Capping the allocator converts that into an immediate, honest OOM.
    # Failing loudly in two minutes is worth far more than succeeding
    # invisibly over four days: peak *allocation* here was only 4.63 GiB, so
    # anything approaching the cap is fragmentation to be recycled, not a
    # working set to be paged.
    if args.vram_fraction and torch.cuda.is_available():
        torch.cuda.set_per_process_memory_fraction(args.vram_fraction)
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"  VRAM capped at {args.vram_fraction:.0%} of {total:.2f} GiB "
              f"= {args.vram_fraction * total:.2f} GiB (spill becomes OOM)")

    args.out.mkdir(parents=True, exist_ok=True)
    history: List[Dict[str, Any]] = []
    model.train()

    step = 0
    micro = 0
    running = 0.0
    running_n = 0
    started = time.perf_counter()
    stop = False
    skip_batches = 0

    # Resume. Adapter weights alone are not a checkpoint: without the
    # optimizer moments and the step counter, "resuming" restarts Adam from
    # zero on a cosine schedule that thinks it is at step 0, which is a
    # different experiment wearing the same name. See _load_checkpoint.
    if args.resume_from:
        state = _load_checkpoint(Path(args.resume_from), model, optimizer)
        step = state["step"]
        history = state.get("history", [])
        skip_batches = step * args.grad_accum
        print(f"  resumed from {args.resume_from} at step {step}; "
              f"fast-forwarding {skip_batches} micro-batches")

    epoch_count = int(math.ceil(args.epochs))
    for _epoch in range(epoch_count):
        if stop:
            break
        for batch in loader:
            # Fast-forward without computing, so a resumed run sees the same
            # examples in the same order as an uninterrupted one would.
            if skip_batches > 0:
                skip_batches -= 1
                continue
            batch = {k: v.to(model.device) for k, v in batch.items()}
            outputs = model(**batch)
            loss = outputs.loss / args.grad_accum
            loss.backward()
            running += outputs.loss.item()
            running_n += 1
            micro += 1

            if micro % args.grad_accum:
                continue

            for group in optimizer.param_groups:
                group["lr"] = lr_at(step)
            torch.nn.utils.clip_grad_norm_(
                [p for p in model.parameters() if p.requires_grad], 1.0
            )
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1

            if step % args.log_every == 0 or step == 1:
                mean = running / max(1, running_n)
                elapsed = time.perf_counter() - started
                rate = step / elapsed if elapsed else 0.0
                eta = (total_steps - step) / rate / 60 if rate else 0.0
                print(f"  step {step:4d}/{total_steps}  loss {mean:.4f}  "
                      f"lr {lr_at(step):.2e}  {vram()}  eta {eta:.1f}m")
                history.append({"step": step, "loss": mean, "lr": lr_at(step),
                                "peak_gib": torch.cuda.max_memory_allocated() / 1024**3
                                if torch.cuda.is_available() else None})
                running, running_n = 0.0, 0

            if args.save_every and step % args.save_every == 0:
                # Versioned, not overwritten. The run-3 stall made the cost of
                # overwriting concrete: a single rolling checkpoint means the
                # only recoverable model is whichever step happened to be last,
                # and it makes comparing step 200 against step 400 impossible
                # after the fact. Evidence from the 200-step checkpoint says
                # the best model may well be an intermediate one, so keeping
                # the trajectory is the point, not a safety net.
                path = _save_checkpoint(args.out, model, optimizer, step, history, args)
                print(f"    checkpoint saved at step {step} -> {path.name}")
                _prune_checkpoints(args.out, keep=args.keep_checkpoints)

            if step >= total_steps:
                stop = True
                break

    print(f"\nsaving adapter to {args.out}")
    model.save_pretrained(str(args.out))
    tokenizer.save_pretrained(str(args.out))

    meta = {
        "base_model": args.model,
        "epochs": args.epochs,
        "optimizer_steps": step,
        "effective_batch": args.batch_size * args.grad_accum,
        "lr": args.lr,
        "lora": {"r": args.lora_r, "alpha": args.lora_alpha, "dropout": args.lora_dropout},
        "max_len": args.max_len,
        "four_bit": not args.no_4bit,
        "grad_checkpoint": not args.no_grad_checkpoint,
        "train_examples": len(dataset),
        "dropped_over_max_len": dataset.dropped,
        "peak_vram_gib": round(torch.cuda.max_memory_allocated() / 1024**3, 2)
        if torch.cuda.is_available() else None,
        "wall_minutes": round((time.perf_counter() - started) / 60, 1),
        "history": history,
    }
    (args.out / "training_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in meta.items() if k != "history"}, indent=2))


def _checkpoint_dirs(out: Path) -> List[Path]:
    """Existing checkpoints, oldest step first."""
    found = []
    for p in out.glob("checkpoint-*"):
        if not p.is_dir():
            continue
        try:
            found.append((int(p.name.split("-")[-1]), p))
        except ValueError:
            continue
    return [p for _, p in sorted(found)]


def _save_checkpoint(out: Path, model, optimizer, step: int,
                     history: List[Dict[str, Any]], args) -> Path:
    """Write a checkpoint that can actually resume training.

    Adapter weights, optimizer moments, the step counter and every RNG stream.
    The RNG states are what make a resumed run produce the same dropout masks
    as an uninterrupted one; without them a resume is reproducible only in the
    loose sense that it trains on the same data.
    """
    import torch

    path = out / f"checkpoint-{step:05d}"
    path.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(path))

    state: Dict[str, Any] = {
        "step": step,
        "history": history,
        "optimizer": optimizer.state_dict(),
        "python_random": random.getstate(),
        "torch_random": torch.get_rng_state(),
        "args": {
            "lr": args.lr, "grad_accum": args.grad_accum, "batch_size": args.batch_size,
            "max_len": args.max_len, "seed": args.seed, "epochs": args.epochs,
            "lora_r": args.lora_r, "lora_alpha": args.lora_alpha,
        },
    }
    try:
        import numpy as np
        state["numpy_random"] = np.random.get_state()
    except ImportError:
        pass
    if torch.cuda.is_available():
        state["cuda_random"] = torch.cuda.get_rng_state_all()

    # Written beside the weights, not into them: a partially written state
    # file must never look like a complete checkpoint.
    tmp = path / "training_state.pt.tmp"
    torch.save(state, tmp)
    tmp.replace(path / "training_state.pt")
    return path


def _load_checkpoint(path: Path, model, optimizer) -> Dict[str, Any]:
    import torch

    state_file = path / "training_state.pt"
    if not state_file.exists():
        raise SystemExit(
            f"{path} has no training_state.pt, so it holds adapter weights but no\n"
            f"optimizer moments, step counter or RNG state. Resuming from it would\n"
            f"restart Adam and the LR schedule from zero -- a different experiment.\n"
            f"Train from scratch instead, or point --resume-from at a full checkpoint."
        )
    state = torch.load(state_file, map_location="cpu", weights_only=False)

    from peft import set_peft_model_state_dict
    from safetensors.torch import load_file

    weights = path / "adapter_model.safetensors"
    if weights.exists():
        set_peft_model_state_dict(model, load_file(str(weights)))
    optimizer.load_state_dict(state["optimizer"])

    random.setstate(state["python_random"])
    torch.set_rng_state(_as_byte_tensor(state["torch_random"]))
    if "numpy_random" in state:
        try:
            import numpy as np
            np.random.set_state(state["numpy_random"])
        except ImportError:
            pass
    if "cuda_random" in state and torch.cuda.is_available():
        try:
            torch.cuda.set_rng_state_all([_as_byte_tensor(s) for s in state["cuda_random"]])
        except Exception as exc:  # pragma: no cover - device-count mismatch
            print(f"  note: could not restore CUDA RNG state ({exc})")
    return state


def _as_byte_tensor(value):
    """torch.set_rng_state insists on a ByteTensor; a checkpoint round-tripped
    through torch.save can hand back something subtly different."""
    import torch

    if isinstance(value, torch.Tensor):
        return value.cpu().to(torch.uint8)
    return torch.tensor(value, dtype=torch.uint8)


def _prune_checkpoints(out: Path, keep: int) -> None:
    """Keep the newest `keep` checkpoints. Disk here runs at 94%."""
    if not keep or keep <= 0:
        return
    import shutil

    existing = _checkpoint_dirs(out)
    for stale in existing[:-keep]:
        try:
            shutil.rmtree(stale)
            print(f"    pruned old checkpoint {stale.name}")
        except OSError as exc:  # pragma: no cover
            print(f"    could not prune {stale.name}: {exc}")


if __name__ == "__main__":
    main()
