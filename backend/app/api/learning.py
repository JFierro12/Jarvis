"""Endpoints for the self-training lifecycle.

Collect, review, and run jobs. The shape of this router is constrained by two
rules that are worth stating because they explain several otherwise-odd
choices:

**No endpoint executes a caller-supplied command.** A job is submitted by
naming a `JobKind` and a typed config; the argv is built server-side from
that. This is the same arrangement `pc-agent` uses for OS commands, for the
same reason, and `test_learning_api.py` asserts no route accepts anything
that reaches a shell.

**No endpoint promotes a model.** There is no deploy route, no settings
mutation, and nothing that writes `REASONING_MODEL`. The comparison report
ends in a recommendation for a human. A model cannot approve its own
deployment here because *nothing* can deploy from here.

Approval of a training example is a human action behind the bearer token. The
assistant can submit a candidate -- that is how a correction gets captured --
but `POST /v1/learning/examples/{id}/approve` is the only route to
`APPROVED`, and for a model-generated example it additionally requires an
independent verifier distinct from the generator.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_approval_credential, require_bearer_token
from app.services.learning import (
    ExampleSource,
    InvalidTransition,
    NotFound,
    ReviewState,
    SelfApproval,
    get_learning_store,
)
from app.services.training_jobs import (
    ROLLBACK_PROCEDURE,
    BuildDatasetConfig,
    CompareReportConfig,
    EvaluateCandidateConfig,
    JobKind,
    TrainCandidateConfig,
    evaluate_promotion,
    get_job_runner,
)

router = APIRouter(prefix="/v1/learning")


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

class SubmitExampleRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    expected: str = Field(default="", max_length=8000)
    source: ExampleSource
    note: str = Field(default="", max_length=1000)
    generator_id: str = Field(default="", max_length=120)


class ExampleResponse(BaseModel):
    id: str
    state: str
    source: str
    reject_reason: Optional[str] = None
    reject_detail: str = ""
    created_at: str


def _as_example_response(example) -> ExampleResponse:
    return ExampleResponse(
        id=example.id,
        state=example.state.value,
        source=example.source.value,
        reject_reason=example.reject_reason.value if example.reject_reason else None,
        reject_detail=example.reject_detail,
        created_at=example.created_at.isoformat(),
    )


@router.post("/examples", response_model=ExampleResponse)
def submit_example(
    payload: SubmitExampleRequest,
    _token: str = Depends(require_bearer_token),
) -> ExampleResponse:
    """Record a candidate example and run the validation gates.

    A 200 here does not mean the example will be trained on. It means the
    example was recorded; `state` says whether it passed the gates, and a
    passing example is `awaiting_review`, never `approved`.
    """
    store = get_learning_store()
    example = store.submit(
        prompt=payload.prompt,
        expected=payload.expected,
        source=payload.source,
        note=payload.note,
        generator_id=payload.generator_id,
    )
    return _as_example_response(example)


@router.get("/examples")
def list_examples(
    state: Optional[ReviewState] = None,
    source: Optional[ExampleSource] = None,
    limit: int = 100,
    _token: str = Depends(require_bearer_token),
) -> Dict[str, object]:
    store = get_learning_store()
    items = store.list(state=state, source=source, limit=min(limit, 500))
    return {
        "examples": [e.as_dict() for e in items],
        "counts": store.counts(),
        "rejections": store.rejection_summary(),
    }


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

class VerificationRequest(BaseModel):
    verifier_id: str = Field(min_length=1, max_length=120)
    passed: bool
    detail: str = Field(default="", max_length=1000)


@router.post("/examples/{example_id}/verify", response_model=ExampleResponse)
def verify_example(
    example_id: str,
    payload: VerificationRequest,
    _token: str = Depends(require_bearer_token),
) -> ExampleResponse:
    """Record an independent check of a model-generated example.

    The verifier names itself so that approval can refuse when the checker and
    the generator turn out to be the same thing.
    """
    try:
        example = get_learning_store().record_verification(
            example_id, payload.verifier_id, passed=payload.passed, detail=payload.detail
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _as_example_response(example)


class ApprovalRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=120)


@router.post("/examples/{example_id}/approve", response_model=ExampleResponse)
def approve_example(
    example_id: str,
    payload: ApprovalRequest,
    _token: str = Depends(require_bearer_token),
    _approval: str = Depends(require_approval_credential),
) -> ExampleResponse:
    """The only route to APPROVED, and it needs the human approval credential.

    Deliberately behind `require_approval_credential` rather than the plain
    bearer token. The assistant holds the API token -- that is how it submits
    a correction in the first place -- so a token-only approval route would
    let the thing being trained decide what it is trained on. The approval
    credential is a separate secret, and it fails closed: with none
    configured there is no approval path at all rather than an open one.
    """
    try:
        example = get_learning_store().approve(example_id, payload.approver)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SelfApproval as exc:
        # 403 rather than 409: this is a refusal on authority grounds, not a
        # state problem that a retry could fix.
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _as_example_response(example)


@router.post("/examples/{example_id}/reject", response_model=ExampleResponse)
def reject_example(
    example_id: str,
    payload: ApprovalRequest,
    _token: str = Depends(require_bearer_token),
) -> ExampleResponse:
    try:
        example = get_learning_store().reject(example_id, payload.approver)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _as_example_response(example)


@router.post("/examples/{example_id}/withdraw", response_model=ExampleResponse)
def withdraw_example(
    example_id: str,
    payload: ApprovalRequest,
    _token: str = Depends(require_bearer_token),
) -> ExampleResponse:
    """Pull an approved example back out of future datasets."""
    try:
        example = get_learning_store().withdraw(example_id, payload.approver)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _as_example_response(example)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class SubmitJobRequest(BaseModel):
    """A job is named, never described as a command.

    Exactly one config field may be set, matching `kind`. Pydantic validates
    the config's own fields and bounds, so an out-of-range learning rate is a
    422 rather than a four-hour run that produces nothing.
    """

    kind: JobKind
    lineage: str = Field(default="default", max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    build_dataset: Optional[BuildDatasetConfig] = None
    train_candidate: Optional[TrainCandidateConfig] = None
    evaluate_candidate: Optional[EvaluateCandidateConfig] = None
    compare_report: Optional[CompareReportConfig] = None

    def config_for_kind(self):
        mapping = {
            JobKind.BUILD_DATASET: self.build_dataset,
            JobKind.TRAIN_CANDIDATE: self.train_candidate,
            JobKind.EVALUATE_CANDIDATE: self.evaluate_candidate,
            JobKind.COMPARE_REPORT: self.compare_report,
        }
        return mapping[self.kind]


@router.post("/jobs")
def submit_job(
    payload: SubmitJobRequest,
    background: BackgroundTasks,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> Dict[str, object]:
    config = payload.config_for_kind()
    if config is None:
        raise HTTPException(
            status_code=422,
            detail=f"kind {payload.kind.value!r} requires a {payload.kind.value} config block",
        )

    runner = get_job_runner()
    job = runner.submit(payload.kind, config, lineage=payload.lineage)

    # Only schedule a worker if the job is actually runnable. A BLOCKED job is
    # already terminal and must not be started by a later drain.
    if job.state.value == "queued":
        # drain, not run_next: a worker that finds the slot busy would
        # otherwise leave this job queued with nobody to start it.
        background.add_task(runner.drain)
    return job.as_dict()


@router.get("/jobs")
def list_jobs(
    limit: int = 50,
    _token: str = Depends(require_bearer_token),
) -> Dict[str, object]:
    runner = get_job_runner()
    active = runner.active()
    return {
        "jobs": [job.as_dict() for job in runner.list(limit=min(limit, 200))],
        "active": active.id if active else None,
    }


@router.get("/jobs/{job_id}")
def job_status(
    job_id: str,
    _token: str = Depends(require_bearer_token),
) -> Dict[str, object]:
    try:
        return get_job_runner().get(job_id).as_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no job {job_id}") from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    _token: str = Depends(require_bearer_token),
) -> Dict[str, object]:
    try:
        return get_job_runner().cancel(job_id).as_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no job {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Promotion review
# ---------------------------------------------------------------------------

class PromotionRequest(BaseModel):
    """Metrics to compare. Behavioural only -- `loss` alone is rejected."""

    candidate: Dict[str, float]
    incumbent: Dict[str, float]
    min_gain: float = Field(default=0.02, ge=0.0, le=1.0)


@router.post("/promotion-review")
def promotion_review(
    payload: PromotionRequest,
    _token: str = Depends(require_bearer_token),
) -> Dict[str, object]:
    """Produce a promotion *recommendation* and the rollback procedure.

    This endpoint changes nothing. It returns a verdict for a human to act on,
    plus the steps to undo a promotion if one is made. Deliberately the end of
    the automated pipeline: the decision to serve a different model is not
    one this service is permitted to take.
    """
    verdict = evaluate_promotion(
        payload.candidate, payload.incumbent, min_gain=payload.min_gain
    )
    body = verdict.as_dict()
    body["rollback_procedure"] = ROLLBACK_PROCEDURE
    body["production_model_unchanged"] = True
    return body


@router.get("/status")
def learning_status(
    _token: str = Depends(require_bearer_token),
) -> Dict[str, object]:
    """One call for a dashboard: queue tally, rejection reasons, job states."""
    store = get_learning_store()
    runner = get_job_runner()
    active = runner.active()
    recent: List[Dict[str, object]] = [
        {
            "id": job.id,
            "kind": job.kind.value,
            "state": job.state.value,
            "detail": job.detail,
        }
        for job in runner.list(limit=10)
    ]
    return {
        "examples": store.counts(),
        "rejections": store.rejection_summary(),
        "approved_ready_to_build": len(store.approved()),
        "active_job": active.id if active else None,
        "recent_jobs": recent,
        "promotion": "manual review only; this service cannot deploy a model",
    }
