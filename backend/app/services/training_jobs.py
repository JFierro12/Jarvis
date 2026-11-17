"""The job lifecycle: build dataset -> train candidate -> evaluate -> report.

This module is modelled on `pc-agent/app/commands.py`, which accepts an
allowlisted command *ID* and maps it to a fixed `argv` list. The same
reasoning applies with more force here, because the thing being launched is a
training run: a caller names a `JobKind`, and this module builds the argv.
There is no code path by which request text -- let alone model output --
becomes part of a command line.

Concretely:

* `JobKind` is a closed enum. An unknown kind is a `KeyError`, not a shell
  string.
* Every parameter is a typed field on a pydantic config with bounds. Numbers
  arrive as numbers; the only free-text field is `notes`, which is recorded in
  the audit record and never passed to a process.
* Paths are not accepted from callers at all. Dataset and output paths are
  derived from the job's own versioned id inside a fixed root, so a caller
  cannot direct a write outside it or overwrite an existing candidate.

**There is deliberately no "promote" or "deploy" job kind.** Switching the
serving model is a manual step, documented by the report job's rollback
section. A candidate cannot approve its own deployment because nothing in this
process is able to deploy anything -- see `test_training_jobs.py`, which
asserts no job kind writes to the settings or model configuration.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as tz
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobKind(str, Enum):
    """The complete set of things this service can run.

    Closed by construction. Adding a kind means adding an argv builder below
    and a test, which is the intended amount of friction.
    """

    BUILD_DATASET = "build_dataset"
    TRAIN_CANDIDATE = "train_candidate"
    EVALUATE_CANDIDATE = "evaluate_candidate"
    COMPARE_REPORT = "compare_report"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    """Refused before starting -- e.g. the lineage has used its attempt
    budget, or a gate found too few approved examples."""


# ---------------------------------------------------------------------------
# Typed configurations
# ---------------------------------------------------------------------------

class JobConfig(BaseModel):
    """Base for every job configuration.

    `extra="forbid"` is the point: an unrecognised key is a 422 rather than a
    silently ignored field. A caller trying to smuggle a `command` or `argv`
    key gets a refusal instead of a job that looks like it honoured it.
    """

    model_config = ConfigDict(extra="forbid")


class BuildDatasetConfig(JobConfig):
    """Render the approved examples into a versioned JSONL dataset."""

    min_examples: int = Field(default=200, ge=1, le=100_000)
    """Below this the build is BLOCKED rather than run. Training on a handful
    of corrections produces a candidate whose evaluation cannot distinguish
    improvement from noise."""

    include_base_dataset: bool = True
    """Mix in the existing curated corpus. Fine-tuning on corrections alone
    is how a model forgets everything it was previously able to do."""

    notes: str = Field(default="", max_length=500)


class TrainCandidateConfig(JobConfig):
    """Hyperparameters for a candidate run.

    Defaults are the verified run-3c configuration, including the VRAM cap
    that turns a silent host-memory spill into an immediate OOM on this 8 GB
    card. Bounds are not decoration: `batch_size` above 1 at this sequence
    length fragmented the allocator past the physical limit and took a run
    from 18 s/step to 15 min/step without erroring.
    """

    dataset_job_id: str = Field(min_length=1, max_length=64)
    base_model: str = Field(default="Qwen/Qwen2.5-3B-Instruct", max_length=120)
    max_len: int = Field(default=720, ge=64, le=4096)
    epochs: int = Field(default=1, ge=1, le=10)
    batch_size: int = Field(default=1, ge=1, le=8)
    grad_accum: int = Field(default=16, ge=1, le=64)
    learning_rate: float = Field(default=1e-4, gt=0.0, le=1e-2)
    lora_r: int = Field(default=32, ge=1, le=256)
    lora_alpha: int = Field(default=64, ge=1, le=512)
    lora_dropout: float = Field(default=0.05, ge=0.0, lt=1.0)
    warmup_ratio: float = Field(default=0.03, ge=0.0, le=0.5)
    save_every: int = Field(default=50, ge=0, le=10_000)
    vram_fraction: float = Field(default=0.82, ge=0.0, le=1.0)
    seed: int = Field(default=20261110, ge=0, le=2**31 - 1)
    resume_from_step: Optional[int] = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=500)


ALLOWED_SPLITS = frozenset(
    {
        "eval_novel",
        "eval_adversarial",
        "eval_seen",
        "eval_asr",
        "eval_clean_matched",
    }
)
"""Splits a routine evaluation may score.

`eval_sealed` is deliberately absent. A sealed split is spent the first time
it is scored -- scoring it twice makes the second number a measurement of
hill-climbing rather than of generalisation -- so it is reserved for the one
promotion decision and is unreachable through this service. The exclusion is
enforced by `Split` being validated against this set, not by remembering.
"""


class EvaluateCandidateConfig(JobConfig):
    """One split, one job.

    `jarvis_train.evaluate` takes a single `--split` and writes exactly one
    report, so a job maps 1:1 onto an invocation rather than a loop this
    service would have to supervise. Several splits means several jobs, which
    is also what makes per-split cancellation meaningful.
    """

    train_job_id: str = Field(min_length=1, max_length=64)
    split: str = Field(default="eval_novel")
    checkpoint_step: Optional[int] = Field(default=None, ge=0)
    base_model: str = Field(default="Qwen/Qwen2.5-3B-Instruct", max_length=120)
    batch: int = Field(default=8, ge=1, le=64)
    max_new_tokens: int = Field(default=128, ge=1, le=2048)
    notes: str = Field(default="", max_length=500)

    @field_validator("split")
    @classmethod
    def _known_split(cls, value: str) -> str:
        if value not in ALLOWED_SPLITS:
            raise ValueError(
                f"{value!r} is not an evaluable split; "
                f"choose one of {sorted(ALLOWED_SPLITS)}"
            )
        return value


class CompareReportConfig(JobConfig):
    """Diff two evaluation reports.

    `jarvis_train.compare` takes a baseline and a candidate as positional
    paths and refuses outright when their eval-set fingerprints differ, so
    this config names two eval jobs and lets that tool do the checking.
    """

    baseline_eval_job_id: str = Field(min_length=1, max_length=64)
    candidate_eval_job_id: str = Field(min_length=1, max_length=64)
    paired: bool = False
    notes: str = Field(default="", max_length=500)


CONFIG_TYPES: Dict[JobKind, type] = {
    JobKind.BUILD_DATASET: BuildDatasetConfig,
    JobKind.TRAIN_CANDIDATE: TrainCandidateConfig,
    JobKind.EVALUATE_CANDIDATE: EvaluateCandidateConfig,
    JobKind.COMPARE_REPORT: CompareReportConfig,
}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_ROOT = REPO_ROOT / "training"
CANDIDATE_ROOT = TRAINING_ROOT / "candidates"
"""Everything this service writes lives under here, keyed by job id.

Separate from `training/out`, which holds the manually-driven runs. A
self-training job cannot overwrite a hand-made artifact because it cannot
name a path at all.
"""

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _job_dir(job_id: str, root: Optional[Path] = None) -> Path:
    """Directory for a job's artifacts.

    The id is pattern-checked even though it is generated internally: this is
    the one place a caller-supplied string (`dataset_job_id`,
    `train_job_id`) becomes part of a filesystem path, so it is validated
    rather than trusted.

    `root` is explicit so a test can point the whole pipeline at a temporary
    directory. Without it the suite created an empty job directory under the
    repo on every run -- harmless individually, ninety of them after an
    afternoon.
    """
    if not _SAFE_ID.match(job_id):
        raise ValueError(f"unsafe job id {job_id!r}")
    return (root or CANDIDATE_ROOT) / job_id


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(tz.utc)


def _new_job_id(kind: JobKind) -> str:
    stamp = _utcnow().strftime("%Y%m%d-%H%M%S")
    return f"{kind.value}-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass
class TrainingJob:
    kind: JobKind
    config: BaseModel
    id: str = ""
    state: JobState = JobState.QUEUED
    created_at: datetime = field(default_factory=_utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    argv: List[str] = field(default_factory=list)
    returncode: Optional[int] = None
    detail: str = ""
    lineage: str = "default"
    artifacts: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    log_path: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_job_id(self.kind)

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.BLOCKED,
        )

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "state": self.state.value,
            "lineage": self.lineage,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "returncode": self.returncode,
            "detail": self.detail,
            "argv": list(self.argv),
            "artifacts": dict(self.artifacts),
            "metrics": dict(self.metrics),
            "log_path": self.log_path,
            "config": json.loads(self.config.model_dump_json()),
        }


# ---------------------------------------------------------------------------
# argv builders -- the only place a command line is constructed
# ---------------------------------------------------------------------------

def _python() -> str:
    """The interpreter to launch training with.

    `sys.executable` is the backend's own venv, which does not carry torch.
    The training venv is used when present, and the absence of it is reported
    as a blocked job rather than a confusing import error from a subprocess.
    """
    candidates = [
        TRAINING_ROOT / ".venv" / "Scripts" / "python.exe",
        TRAINING_ROOT / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _build_dataset_argv(job: TrainingJob, root: Optional[Path] = None) -> List[str]:
    config: BuildDatasetConfig = job.config  # type: ignore[assignment]
    out = _job_dir(job.id, root) / "train.jsonl"
    argv = [
        _python(), "-u", "-m", "jarvis_train.build_candidate_dataset",
        "--approved", str(_job_dir(job.id, root) / "approved.jsonl"),
        "--out", str(out),
        "--min-examples", str(config.min_examples),
    ]
    if config.include_base_dataset:
        argv += ["--base", str(TRAINING_ROOT / "data-v3" / "train.jsonl")]
    else:
        # The builder refuses a corrections-only set unless told explicitly,
        # because training on only the cases the model got wrong is how it
        # forgets what it already did right. Opting out has to be said out
        # loud, here and in the config.
        argv.append("--allow-corrections-only")
    return argv


def _train_candidate_argv(job: TrainingJob, root: Optional[Path] = None) -> List[str]:
    config: TrainCandidateConfig = job.config  # type: ignore[assignment]
    dataset = _job_dir(config.dataset_job_id, root) / "train.jsonl"
    out = _job_dir(job.id, root) / "adapter"
    argv = [
        _python(), "-u", "-m", "jarvis_train.train_qlora",
        "--train", str(dataset),
        "--out", str(out),
        "--model", config.base_model,
        "--max-len", str(config.max_len),
        "--epochs", str(config.epochs),
        "--batch-size", str(config.batch_size),
        "--grad-accum", str(config.grad_accum),
        "--lr", repr(config.learning_rate),
        "--lora-r", str(config.lora_r),
        "--lora-alpha", str(config.lora_alpha),
        "--lora-dropout", repr(config.lora_dropout),
        "--warmup-ratio", repr(config.warmup_ratio),
        "--save-every", str(config.save_every),
        "--vram-fraction", repr(config.vram_fraction),
        "--seed", str(config.seed),
        "--log-every", "10",
    ]
    if config.resume_from_step is not None:
        argv += ["--resume-from", str(out / f"checkpoint-{config.resume_from_step:05d}")]
    return argv


def eval_report_path(job_id: str) -> Path:
    """Where `jarvis_train.evaluate` writes the report for a job.

    Not chosen here: the tool derives `out/report-{tag}.json` from `--tag`
    itself and takes no output flag. Passing the job id as the tag makes the
    path predictable, and this function is the one place that knowledge lives
    so a change to the tool's naming breaks in a single spot.
    """
    if not _SAFE_ID.match(job_id):
        raise ValueError(f"unsafe job id {job_id!r}")
    return TRAINING_ROOT / "out" / f"report-{job_id}.json"


def _evaluate_candidate_argv(job: TrainingJob, root: Optional[Path] = None) -> List[str]:
    config: EvaluateCandidateConfig = job.config  # type: ignore[assignment]
    adapter = _job_dir(config.train_job_id, root) / "adapter"
    if config.checkpoint_step is not None:
        adapter = adapter / f"checkpoint-{config.checkpoint_step:05d}"
    return [
        _python(), "-u", "-m", "jarvis_train.evaluate",
        "--backend", "hf",
        "--model", config.base_model,
        "--adapter", str(adapter),
        "--split", config.split,
        "--data", str(TRAINING_ROOT / "data-v3" / "frozen"),
        "--batch", str(config.batch),
        "--max-new-tokens", str(config.max_new_tokens),
        "--latency-sample", "0",
        "--tag", job.id,
    ]


def _compare_report_argv(job: TrainingJob, root: Optional[Path] = None) -> List[str]:
    config: CompareReportConfig = job.config  # type: ignore[assignment]
    argv = [
        _python(), "-u", "-m", "jarvis_train.compare",
        str(eval_report_path(config.baseline_eval_job_id)),
        str(eval_report_path(config.candidate_eval_job_id)),
    ]
    if config.paired:
        argv.append("--paired")
    # `--force` is never passed. It suppresses the eval-set fingerprint
    # refusal, which is the check that stops two different test sets being
    # tabulated as one comparison. An automated pipeline is precisely where
    # that mistake would go unnoticed.
    return argv


ARGV_BUILDERS = {
    JobKind.BUILD_DATASET: _build_dataset_argv,
    JobKind.TRAIN_CANDIDATE: _train_candidate_argv,
    JobKind.EVALUATE_CANDIDATE: _evaluate_candidate_argv,
    JobKind.COMPARE_REPORT: _compare_report_argv,
}


def build_argv(job: TrainingJob, root: Optional[Path] = None) -> List[str]:
    builder = ARGV_BUILDERS.get(job.kind)
    if builder is None:  # pragma: no cover - JobKind is closed
        raise KeyError(job.kind)
    return builder(job, root)


# ---------------------------------------------------------------------------
# Promotion gates
# ---------------------------------------------------------------------------

MAX_CONSECUTIVE_FAILURES = 3
"""After this many failed candidates in a lineage the pipeline stops.

The brief's "do not enter an unlimited retraining loop" as a number. A fourth
attempt is BLOCKED until someone records a diagnosis, because repeating a
failing experiment is not an investigation.
"""


@dataclass(frozen=True)
class PromotionVerdict:
    """The output of comparing a candidate to the incumbent.

    `promote` being True is a *recommendation for review*, never an action.
    Nothing in this process can switch the serving model.
    """

    promote: bool
    reasons: List[str]
    regressions: List[str]

    def as_dict(self) -> Dict[str, object]:
        return {
            "recommend_promotion": self.promote,
            "reasons": list(self.reasons),
            "regressions": list(self.regressions),
            "decision": "requires human review -- this process cannot deploy",
        }


def evaluate_promotion(
    candidate: Dict[str, float],
    incumbent: Dict[str, float],
    *,
    min_gain: float = 0.02,
) -> PromotionVerdict:
    """Compare candidate metrics against the incumbent's.

    Training loss is explicitly ignored: a run can drive loss to 0.02 by
    memorising the output format while getting *worse* at ordinary questions,
    which is exactly what happened at 412 steps. The criteria are behavioural.

    A candidate must not regress on general competence to win on routing.
    That trade is what produced a model worse than its own base at answering
    questions, so `answered_correctly` regressing is disqualifying regardless
    of how much routing improved.
    """
    reasons: List[str] = []
    regressions: List[str] = []

    if "loss" in candidate and len(candidate) == 1:
        return PromotionVerdict(
            False,
            ["loss is not a promotion criterion; behavioural metrics are required"],
            [],
        )

    guarded = ("answered_correctly", "injection_obeyed", "correct_tool")
    for metric in guarded:
        if metric not in candidate or metric not in incumbent:
            continue
        delta = candidate[metric] - incumbent[metric]
        if metric == "injection_obeyed":
            # Lower is better, and any increase is disqualifying.
            if delta > 0:
                regressions.append(
                    f"injection_obeyed rose {incumbent[metric]:.0f} -> {candidate[metric]:.0f}"
                )
            continue
        if delta < -0.005:
            regressions.append(
                f"{metric} fell {incumbent[metric]:.3f} -> {candidate[metric]:.3f}"
            )
        elif delta >= min_gain:
            reasons.append(
                f"{metric} rose {incumbent[metric]:.3f} -> {candidate[metric]:.3f}"
            )

    if regressions:
        return PromotionVerdict(False, reasons, regressions)
    if not reasons:
        return PromotionVerdict(
            False, ["no metric improved by the required margin"], regressions
        )
    return PromotionVerdict(True, reasons, regressions)


def evaluate_promotion_across_splits(
    candidate: Dict[str, Dict[str, float]],
    incumbent: Dict[str, Dict[str, float]],
    *,
    min_gain: float = 0.02,
) -> PromotionVerdict:
    """Compare per-split metrics, requiring no split to regress.

    `evaluate_promotion` takes a flat metric dict with no notion of which
    evaluation split produced it, which makes it possible to recommend a
    candidate on one split while it regresses on another. That is not
    hypothetical: run 3c step 500 gained 1.7 points of general competence on
    `eval_novel` while losing 3.3 points of routing accuracy and 3.0 points of
    confirmation-flag accuracy on `eval_adversarial`. A novel-only comparison
    with a slightly larger gain would have recommended it.

    Adversarial performance is where loss of generalisation shows up first:
    paraphrased, misspelled and ASR-mangled input is the part of the
    distribution a memorising model stops handling. So a gain on the easier
    split never excuses a regression on the harder one, and the splits are
    evaluated separately rather than averaged -- averaging lets a large
    in-distribution gain hide a safety-relevant regression.

    A split measured on only one side is reported as a regression rather than
    ignored. Treating "not measured" as "did not regress" is precisely the
    failure this function exists to prevent.
    """
    reasons: List[str] = []
    regressions: List[str] = []
    unmeasured: List[str] = []

    for split in sorted(set(candidate) | set(incumbent)):
        if split not in candidate or split not in incumbent:
            unmeasured.append(split)
            continue
        verdict = evaluate_promotion(
            candidate[split], incumbent[split], min_gain=min_gain
        )
        reasons.extend("[" + split + "] " + r for r in verdict.reasons)
        regressions.extend("[" + split + "] " + r for r in verdict.regressions)

    if unmeasured:
        regressions.append("not measured on both sides for: " + ", ".join(unmeasured))

    if regressions:
        return PromotionVerdict(False, reasons, regressions)

    gains = [
        r for r in reasons
        if "not a promotion criterion" not in r and "required margin" not in r
    ]
    if not gains:
        return PromotionVerdict(
            False, reasons or ["no split improved by the required margin"], []
        )
    return PromotionVerdict(True, reasons, regressions)



ROLLBACK_PROCEDURE = """\
## Rollback

The serving model is unchanged by this pipeline; promotion is a manual edit.
To undo one:

1. Restore the previous value of `REASONING_MODEL` in `backend/.env`.
2. Restart the backend. Nothing else holds a reference to the adapter.
3. The previous GGUF remains registered in Ollama under its own tag; no
   re-export is needed. Confirm with `ollama list`.

No candidate artifact is deleted by a rollback, so the comparison can be
re-run afterwards.
"""


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

class JobRunner:
    """Tracks jobs and launches them one at a time.

    Single-slot by design: this host has one 8 GB GPU, and two concurrent
    training runs would spill into host memory and silently crawl rather than
    fail. A second submission while one is running is QUEUED, not started.
    """

    def __init__(self, launcher=None, root: Optional[Path] = None) -> None:
        self._root = root or CANDIDATE_ROOT
        self._lock = threading.Lock()
        self._jobs: Dict[str, TrainingJob] = {}
        self._order: List[str] = []
        self._launcher = launcher
        self._active: Optional[str] = None

    # -- submit -----------------------------------------------------------

    def submit(
        self,
        kind: JobKind,
        config: BaseModel,
        *,
        lineage: str = "default",
    ) -> TrainingJob:
        expected = CONFIG_TYPES[kind]
        if not isinstance(config, expected):
            raise TypeError(f"{kind.value} needs {expected.__name__}")

        job = TrainingJob(kind=kind, config=config, lineage=lineage)

        with self._lock:
            blocked = self._blocked_reason_locked(job)
            if blocked:
                job.state = JobState.BLOCKED
                job.detail = blocked
                self._jobs[job.id] = job
                self._order.append(job.id)
                return job

            try:
                job.argv = build_argv(job, self._root)
            except (ValueError, KeyError) as exc:
                job.state = JobState.BLOCKED
                job.detail = f"could not build a command line: {exc}"
                self._jobs[job.id] = job
                self._order.append(job.id)
                return job

            self._jobs[job.id] = job
            self._order.append(job.id)
        return job

    def _blocked_reason_locked(self, job: TrainingJob) -> str:
        failures = 0
        for job_id in reversed(self._order):
            other = self._jobs[job_id]
            if other.lineage != job.lineage:
                continue
            if other.kind is not JobKind.TRAIN_CANDIDATE:
                continue
            if other.state is JobState.FAILED:
                failures += 1
            elif other.state is JobState.SUCCEEDED:
                break
        if job.kind is JobKind.TRAIN_CANDIDATE and failures >= MAX_CONSECUTIVE_FAILURES:
            return (
                f"lineage {job.lineage!r} has {failures} consecutive failed training "
                "runs; record a diagnosis before trying again"
            )
        return ""

    # -- control ----------------------------------------------------------

    def cancel(self, job_id: str) -> TrainingJob:
        with self._lock:
            job = self._require_locked(job_id)
            if job.is_terminal:
                raise ValueError(f"job {job_id} already finished as {job.state.value}")
            job.state = JobState.CANCELLED
            job.finished_at = _utcnow()
            job.detail = "cancelled by request"
            if self._active == job_id:
                self._active = None
            return job

    def _require_locked(self, job_id: str) -> TrainingJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    # -- read -------------------------------------------------------------

    def get(self, job_id: str) -> TrainingJob:
        with self._lock:
            return self._require_locked(job_id)

    def list(self, *, limit: int = 50) -> List[TrainingJob]:
        with self._lock:
            jobs = [self._jobs[j] for j in reversed(self._order)]
        return jobs[:limit]

    def active(self) -> Optional[TrainingJob]:
        with self._lock:
            if self._active is None:
                return None
            return self._jobs.get(self._active)

    # -- execute ----------------------------------------------------------

    def run_next(self) -> Optional[TrainingJob]:
        """Start the oldest queued job, if the single slot is free.

        Returns the job it ran, or None when there is nothing to do or the
        slot is occupied. Synchronous: the API schedules this on a worker so
        a request is never held open for the length of a training run.
        """
        with self._lock:
            if self._active is not None:
                return None
            next_id = next(
                (j for j in self._order if self._jobs[j].state is JobState.QUEUED),
                None,
            )
            if next_id is None:
                return None
            job = self._jobs[next_id]
            job.state = JobState.RUNNING
            job.started_at = _utcnow()
            self._active = job.id

        try:
            self._execute(job)
        finally:
            with self._lock:
                if self._active == job.id:
                    self._active = None
        return job


    def drain(self, max_jobs: int = 64) -> List[TrainingJob]:
        """Run queued jobs one at a time until none remain.

        `run_next` on its own leaves jobs orphaned. The API schedules one
        worker per submission, and a worker that finds the single slot
        occupied returns immediately -- so a job submitted *while another is
        running* has nobody left to start it. That is the normal case, not an
        edge one: a train job is submitted right after the build job it
        depends on.

        Draining in a loop means whichever worker holds the slot also picks up
        everything queued behind it. A second concurrent drain finds the slot
        busy, returns nothing and exits harmlessly, because the first one is
        still working through the queue.

        `max_jobs` bounds the loop so a pathological requeue cannot spin
        forever.
        """
        ran: List[TrainingJob] = []
        for _ in range(max_jobs):
            job = self.run_next()
            if job is None:
                break
            ran.append(job)
        return ran

    def _execute(self, job: TrainingJob) -> None:
        job_dir = _job_dir(job.id, self._root)
        job_dir.mkdir(parents=True, exist_ok=True)
        log_path = job_dir / "job.log"
        job.log_path = str(log_path)

        if job.kind is JobKind.BUILD_DATASET:
            written = self._export_approved(job_dir)
            if written == 0:
                job.state = JobState.BLOCKED
                job.detail = (
                    "no approved examples to build from; review the queue first"
                )
                job.finished_at = _utcnow()
                return

        launcher = self._launcher or _default_launcher
        try:
            returncode, output = launcher(job.argv, TRAINING_ROOT, log_path)
        except FileNotFoundError as exc:
            job.state = JobState.FAILED
            job.returncode = None
            job.detail = f"could not launch: {exc}"
            job.finished_at = _utcnow()
            return

        job.returncode = returncode
        job.finished_at = _utcnow()
        if returncode == 0:
            job.state = JobState.SUCCEEDED
            job.detail = "completed"
            job.artifacts = _discover_artifacts(job_dir, job)
        else:
            job.state = JobState.FAILED
            job.detail = _failure_summary(output)


    @staticmethod
    def _export_approved(job_dir: Path) -> int:
        """Snapshot the approved examples into the job directory.

        The dataset builder reads a file rather than the live queue, so the
        rows a candidate was trained on stay pinned next to that candidate.
        Re-running a build later, after the queue has moved on, produces a
        different dataset -- and this is what makes that visible instead of
        mysterious.

        Imported here rather than at module scope: the queue imports nothing
        from this module, and keeping the dependency one-directional means a
        job cannot be the thing that mutates review state.
        """
        from app.services.anthropic_reasoning import TUNED_SYSTEM_PROMPT
        from app.services.learning import get_learning_store

        store = get_learning_store()
        rows = store.export_approved(TUNED_SYSTEM_PROMPT)
        path = job_dir / "approved.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return len(rows)


def _default_launcher(argv: Sequence[str], cwd: Path, log_path: Path):
    """Run a job's fixed argv list.

    Always a list, never `shell=True`, never a string. `cwd` is the training
    root rather than the caller's directory so a relative module import
    resolves the same way it does for a hand-run job.
    """
    with log_path.open("w", encoding="utf-8") as handle:
        process = subprocess.run(
            list(argv),
            cwd=str(cwd),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    try:
        output = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive
        output = ""
    return process.returncode, output


def _failure_summary(output: str) -> str:
    """The line most likely to explain a failure.

    Prefers a torch OOM, then a traceback's final line, because a job log's
    last line is often a progress bar rather than the error.
    """
    if not output:
        return "failed with no output"
    if "CUDA out of memory" in output:
        return "CUDA out of memory -- lower batch_size or vram_fraction"
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if re.match(r"^[A-Za-z_.]+(?:Error|Exception|Exit)\b", line):
            return line[:400]
    return lines[-1][:400]


def _discover_artifacts(job_dir: Path, job: TrainingJob) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for name in ("train.jsonl", "approved.jsonl", "dataset_report.json"):
        path = job_dir / name
        if path.exists():
            found[name] = str(path)

    adapter = job_dir / "adapter"
    if adapter.is_dir():
        found["adapter"] = str(adapter)
        checkpoints = sorted(
            p.name for p in adapter.iterdir() if p.name.startswith("checkpoint-")
        )
        if checkpoints:
            found["checkpoints"] = ",".join(checkpoints)

    # An eval report is written by the tool into `training/out`, not into the
    # job directory, so it has to be looked up rather than scanned for.
    if job.kind is JobKind.EVALUATE_CANDIDATE:
        report = eval_report_path(job.id)
        if report.exists():
            found["report"] = str(report)

    # `jarvis_train.compare` prints its table to stdout and writes no file,
    # so for a comparison the captured log *is* the report. Naming it as such
    # is more honest than reporting no artifact.
    if job.kind is JobKind.COMPARE_REPORT and job.log_path:
        found["comparison"] = job.log_path

    return found


_runner: Optional[JobRunner] = None
_runner_lock = threading.Lock()


def get_job_runner() -> JobRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = JobRunner()
        return _runner


def reset_job_runner_for_tests() -> None:
    global _runner
    with _runner_lock:
        _runner = None


__all__ = [
    "ALLOWED_SPLITS",
    "ARGV_BUILDERS",
    "BuildDatasetConfig",
    "CANDIDATE_ROOT",
    "CONFIG_TYPES",
    "CompareReportConfig",
    "EvaluateCandidateConfig",
    "JobConfig",
    "JobKind",
    "JobRunner",
    "JobState",
    "MAX_CONSECUTIVE_FAILURES",
    "PromotionVerdict",
    "ROLLBACK_PROCEDURE",
    "TrainCandidateConfig",
    "TrainingJob",
    "build_argv",
    "evaluate_promotion",
    "evaluate_promotion_across_splits",
    "get_job_runner",
    "reset_job_runner_for_tests",
]
