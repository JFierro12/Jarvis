"""Tests for the training job runner.

The job runner launches subprocesses, so most of these tests are about what it
*cannot* be made to launch, and about the gates that stop a pipeline from
promoting a model on the strength of a falling loss curve.
"""

import ast
import inspect
import json

import pytest
from pydantic import ValidationError

from app.services import training_jobs as tj
from app.services.training_jobs import (
    ARGV_BUILDERS,
    MAX_CONSECUTIVE_FAILURES,
    BuildDatasetConfig,
    CompareReportConfig,
    EvaluateCandidateConfig,
    JobKind,
    JobRunner,
    JobState,
    TrainCandidateConfig,
    TrainingJob,
    build_argv,
    evaluate_promotion,
    evaluate_promotion_across_splits,
)


@pytest.fixture()
def runner(tmp_path):
    """A runner whose artifacts land in a temp directory.

    Passing `root` matters: without it every run of this suite left an empty
    job directory under `training/candidates` in the working tree.
    """
    return JobRunner(launcher=lambda argv, cwd, log: (0, "done"), root=tmp_path)


def a_train_config(**kwargs):
    base = {"dataset_job_id": "build_dataset-20261113-000000-abcdef"}
    base.update(kwargs)
    return TrainCandidateConfig(**base)


# ---------------------------------------------------------------------------
# No arbitrary execution
# ---------------------------------------------------------------------------

class TestNoArbitraryExecution:
    def test_every_job_kind_has_a_fixed_argv_builder(self):
        """A caller names a kind; this module builds the command line.

        Same arrangement as `pc-agent/app/commands.py`: allowlisted IDs mapped
        to fixed argv, never a command string from a request.
        """
        assert set(ARGV_BUILDERS) == set(JobKind)

    def test_an_unknown_kind_cannot_be_submitted(self):
        with pytest.raises(ValueError):
            JobKind("rm -rf /")

    def test_no_config_field_reaches_the_command_line_as_free_text(self):
        """`notes` is the only free-text field, and it must stay out of argv.

        Everything else is typed and bounded, so there is no string a caller
        controls that becomes a command-line token.
        """
        marker = "; rm -rf / #"
        for kind, config in (
            (JobKind.BUILD_DATASET, BuildDatasetConfig(notes=marker)),
            (JobKind.TRAIN_CANDIDATE, a_train_config(notes=marker)),
            (
                JobKind.EVALUATE_CANDIDATE,
                EvaluateCandidateConfig(train_job_id="train_candidate-1", notes=marker),
            ),
            (
                JobKind.COMPARE_REPORT,
                CompareReportConfig(
                    baseline_eval_job_id="evaluate_candidate-1",
                    candidate_eval_job_id="evaluate_candidate-2",
                    notes=marker,
                ),
            ),
        ):
            argv = build_argv(TrainingJob(kind=kind, config=config))
            assert all(marker not in token for token in argv), (
                f"{kind.value} leaked its notes field into argv"
            )

    def test_the_launcher_never_uses_a_shell(self):
        """Asserted against the parsed syntax tree, not the source text.

        A text grep for "shell=True" also matches the comment explaining why
        it is not used, so it fails on its own documentation. The AST carries
        no comments or docstrings, which makes it the honest thing to assert
        on: these are claims about the code, so they are checked against the
        code.
        """
        tree = ast.parse(inspect.getsource(tj))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = ast.unparse(node.func)
            if "subprocess" not in target and not target.endswith("system"):
                continue
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    offenders.append(f"{target}(shell=...)")
            if node.args and isinstance(node.args[0], ast.Constant):
                offenders.append(f"{target} called with a string command")
        assert offenders == [], f"shell execution reachable via {offenders}"

    def test_the_launcher_passes_argv_as_a_list(self):
        tree = ast.parse(inspect.getsource(tj._default_launcher))
        run_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "subprocess.run"
        ]
        assert run_calls, "no subprocess.run call found to check"
        first_arg = ast.unparse(run_calls[0].args[0])
        assert first_arg == "list(argv)", (
            f"subprocess.run received {first_arg!r}; it must be an explicit list"
        )

    def test_argv_is_always_a_list_of_strings(self):
        argv = build_argv(TrainingJob(kind=JobKind.TRAIN_CANDIDATE,
                                      config=a_train_config()))
        assert isinstance(argv, list)
        assert all(isinstance(token, str) for token in argv)

    def test_a_job_id_with_path_traversal_is_refused(self):
        """Job ids become filesystem paths, so they are pattern-checked.

        `dataset_job_id` and `train_job_id` are the only caller-supplied
        strings that reach a path, which is why they are validated rather than
        trusted despite normally being generated internally.
        """
        config = a_train_config(dataset_job_id="../../../etc/passwd")
        with pytest.raises(ValueError, match="unsafe job id"):
            build_argv(TrainingJob(kind=JobKind.TRAIN_CANDIDATE, config=config))

    def test_a_traversing_id_blocks_the_job_rather_than_crashing(self, runner):
        job = runner.submit(JobKind.TRAIN_CANDIDATE,
                            a_train_config(dataset_job_id="../../evil"))
        assert job.state is JobState.BLOCKED
        assert "command line" in job.detail

    def test_all_writes_stay_under_the_candidate_root(self):
        """A job cannot be directed to overwrite a hand-made artifact.

        `training/out` holds the manually-driven runs. Self-training writes
        live under `training/candidates/<job id>`, and the caller never names
        a path at all.
        """
        job = TrainingJob(kind=JobKind.BUILD_DATASET, config=BuildDatasetConfig())
        argv = build_argv(job)
        out_index = argv.index("--out") + 1
        assert str(tj.CANDIDATE_ROOT) in argv[out_index]
        assert job.id in argv[out_index]


class TestCannotDeploy:
    def test_there_is_no_promote_or_deploy_job_kind(self):
        """A candidate cannot approve its own deployment because nothing here
        can deploy. Asserted on the enum rather than trusted."""
        kinds = {kind.value for kind in JobKind}
        for forbidden in ("promote", "deploy", "serve", "switch_model", "install"):
            assert not any(forbidden in kind for kind in kinds), (
                f"a {forbidden!r} job kind appeared; promotion must stay manual"
            )

    def test_no_job_writes_the_serving_model_configuration(self):
        """This module must have no way to change what is served.

        Checked as three structural facts rather than a text search: the text
        "REASONING_MODEL" legitimately appears in the published rollback
        procedure, so grepping for it would fail on the very documentation
        that tells a human how to undo a promotion.
        """
        tree = ast.parse(inspect.getsource(tj))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert "app.core.config" not in imported, (
            "training_jobs imports the settings module; it could then mutate "
            "what model is served"
        )
        assert not any(name.startswith("os") for name in imported), (
            "training_jobs imports os; environment mutation becomes reachable"
        )

        # No assignment into a process environment or a settings object.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    rendered = ast.unparse(target)
                    assert "environ" not in rendered, (
                        f"assignment into the environment: {rendered}"
                    )
                    assert "settings" not in rendered.lower(), (
                        f"assignment into settings: {rendered}"
                    )

        # Nothing opens a dotenv file for writing.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("open"):
                rendered = ast.unparse(node)
                assert ".env" not in rendered, f"writes a dotenv file: {rendered}"

    def test_the_verdict_says_a_human_decides(self):
        verdict = evaluate_promotion(
            {"answered_correctly": 0.90, "correct_tool": 0.85},
            {"answered_correctly": 0.75, "correct_tool": 0.80},
        )
        assert verdict.promote is True
        assert "human" in verdict.as_dict()["decision"]

    def test_a_rollback_procedure_is_published(self):
        assert "REASONING_MODEL" in tj.ROLLBACK_PROCEDURE
        assert "Restart the backend" in tj.ROLLBACK_PROCEDURE


# ---------------------------------------------------------------------------
# Promotion gates
# ---------------------------------------------------------------------------

class TestPromotionGates:
    def test_loss_alone_is_not_a_promotion_criterion(self):
        """The headline rule.

        Run 3c drove training loss to 0.024 while, at a comparable point, its
        predecessor had become worse than its own base model at ordinary
        questions. A pipeline that promoted on loss would have shipped that.
        """
        verdict = evaluate_promotion({"loss": 0.01}, {"loss": 0.5})
        assert verdict.promote is False
        assert "loss is not a promotion criterion" in verdict.reasons[0]

    def test_a_general_competence_regression_disqualifies(self):
        """Routing gains must not be bought with general-competence losses.

        This is exactly the run-2 trade: routing 90.6%, answered 55.5%,
        against an untuned baseline that answered 74.9%.
        """
        verdict = evaluate_promotion(
            {"correct_tool": 0.906, "answered_correctly": 0.555},
            {"correct_tool": 0.247, "answered_correctly": 0.749},
        )
        assert verdict.promote is False
        assert any("answered_correctly fell" in r for r in verdict.regressions)

    def test_the_c200_shaped_result_is_promotable(self):
        """The shape worth wanting: better at both, against the untuned base."""
        verdict = evaluate_promotion(
            {"correct_tool": 0.790, "answered_correctly": 0.874, "injection_obeyed": 0},
            {"correct_tool": 0.247, "answered_correctly": 0.749, "injection_obeyed": 2},
        )
        assert verdict.promote is True

    def test_any_increase_in_obeyed_injections_disqualifies(self):
        """Not a rate with a tolerance: a single new obeyed injection blocks.

        An assistant that executes an instruction found in retrieved content
        has a security defect, and no accuracy gain compensates.
        """
        verdict = evaluate_promotion(
            {"answered_correctly": 0.95, "injection_obeyed": 1},
            {"answered_correctly": 0.70, "injection_obeyed": 0},
        )
        assert verdict.promote is False
        assert any("injection_obeyed rose" in r for r in verdict.regressions)

    def test_a_marginal_gain_does_not_promote(self):
        verdict = evaluate_promotion(
            {"answered_correctly": 0.752}, {"answered_correctly": 0.749}
        )
        assert verdict.promote is False
        assert "required margin" in verdict.reasons[0]

    def test_a_tiny_regression_is_tolerated_but_wins_nothing(self):
        verdict = evaluate_promotion(
            {"answered_correctly": 0.8, "correct_tool": 0.748},
            {"answered_correctly": 0.7, "correct_tool": 0.749},
        )
        assert verdict.promote is True, "a 0.001 wobble should not block a 0.1 gain"


# ---------------------------------------------------------------------------
# Typed configuration
# ---------------------------------------------------------------------------

class TestTypedConfigs:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("batch_size", 0),
            ("batch_size", 99),
            ("learning_rate", 0.0),
            ("learning_rate", 1.0),
            ("max_len", 10),
            ("epochs", 0),
            ("vram_fraction", 1.5),
            ("lora_dropout", 1.0),
        ],
    )
    def test_out_of_range_hyperparameters_are_rejected(self, field, value):
        """A bad value is a 422, not a four-hour run that produces nothing."""
        with pytest.raises(ValidationError):
            a_train_config(**{field: value})

    def test_defaults_are_the_verified_run_3c_configuration(self):
        """The config that actually trained on this 8 GB card.

        batch_size 1 with a 0.82 VRAM cap. Batch 2 fragmented the allocator
        past the physical limit and spilled into host memory over PCIe --
        no error, 100% reported utilisation, and a 51x slowdown.
        """
        config = a_train_config()
        assert config.batch_size == 1
        assert config.grad_accum == 16
        assert config.vram_fraction == pytest.approx(0.82)
        assert config.max_len == 720

    def test_the_sealed_split_cannot_be_evaluated(self):
        """A sealed split is spent the first time it is scored.

        Reserved for the single promotion decision, so routine evaluation
        cannot reach it -- otherwise the number becomes a measure of
        hill-climbing rather than generalisation.
        """
        with pytest.raises(ValidationError):
            EvaluateCandidateConfig(train_job_id="t1", split="eval_sealed")

    def test_a_made_up_split_is_rejected(self):
        with pytest.raises(ValidationError):
            EvaluateCandidateConfig(train_job_id="t1", split="eval_easy")

    def test_known_splits_are_accepted(self):
        for split in ("eval_novel", "eval_adversarial", "eval_asr"):
            assert EvaluateCandidateConfig(train_job_id="t1", split=split).split == split

    def test_submitting_the_wrong_config_type_is_a_type_error(self, runner):
        with pytest.raises(TypeError, match="needs"):
            runner.submit(JobKind.TRAIN_CANDIDATE, BuildDatasetConfig())

    def test_a_corrections_only_build_must_say_so_explicitly(self):
        """Training on only the cases the model got wrong causes forgetting.

        The builder refuses unless the flag is passed, so opting in is
        deliberate rather than the result of leaving a field unset.
        """
        argv = build_argv(TrainingJob(
            kind=JobKind.BUILD_DATASET,
            config=BuildDatasetConfig(include_base_dataset=False),
        ))
        assert "--allow-corrections-only" in argv
        assert "--base" not in argv

    def test_the_default_build_mixes_in_the_curated_corpus(self):
        argv = build_argv(TrainingJob(kind=JobKind.BUILD_DATASET,
                                      config=BuildDatasetConfig()))
        assert "--base" in argv
        assert "--allow-corrections-only" not in argv

    def test_compare_never_passes_force(self):
        """`--force` suppresses the eval-set fingerprint refusal.

        That check is what stops two different test sets being tabulated as
        one comparison -- the mistake most likely to go unnoticed in an
        automated pipeline.
        """
        argv = build_argv(TrainingJob(
            kind=JobKind.COMPARE_REPORT,
            config=CompareReportConfig(baseline_eval_job_id="a",
                                       candidate_eval_job_id="b"),
        ))
        assert "--force" not in argv


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_a_submitted_job_is_queued(self, runner):
        job = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        assert job.state is JobState.QUEUED
        assert job.id.startswith("train_candidate-")

    def test_running_a_job_marks_it_succeeded(self, runner):
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        job = runner.run_next()
        assert job.state is JobState.SUCCEEDED
        assert job.returncode == 0

    def test_a_failing_job_reports_a_useful_summary(self, tmp_path):
        runner = JobRunner(
            launcher=lambda argv, cwd, log: (1, "torch.cuda.OutOfMemoryError: CUDA out of memory"),
            root=tmp_path,
        )
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        job = runner.run_next()
        assert job.state is JobState.FAILED
        assert "lower batch_size" in job.detail

    def test_only_one_job_runs_at_a_time(self, runner):
        """One 8 GB GPU. Two concurrent runs would both spill to host memory
        and crawl rather than fail, which is the worst available outcome."""
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        first = runner.run_next()
        assert first is not None
        assert runner.active() is None, "the slot should be free once run_next returns"
        queued = [j for j in runner.list() if j.state is JobState.QUEUED]
        assert len(queued) == 1

    def test_cancelling_a_queued_job_stops_it_running(self, runner):
        job = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        runner.cancel(job.id)
        assert runner.run_next() is None

    def test_cancelling_a_finished_job_is_an_error(self, runner):
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        job = runner.run_next()
        with pytest.raises(ValueError, match="already finished"):
            runner.cancel(job.id)

    def test_an_unknown_job_id_raises(self, runner):
        with pytest.raises(KeyError):
            runner.get("nope")

    def test_resume_passes_a_checkpoint_path(self):
        """Adapter weights alone are not a resumable checkpoint.

        The trainer writes optimizer, scheduler and RNG state alongside them;
        this only has to point at the directory.
        """
        argv = build_argv(TrainingJob(kind=JobKind.TRAIN_CANDIDATE,
                                      config=a_train_config(resume_from_step=250)))
        assert "--resume-from" in argv
        assert "checkpoint-00250" in argv[argv.index("--resume-from") + 1]


class TestNoUnlimitedRetraining:
    def test_a_lineage_is_blocked_after_repeated_failures(self, tmp_path):
        """"Do not enter an unlimited retraining loop", as a number.

        Repeating a failing experiment is not an investigation, so the fourth
        attempt is refused until someone records a diagnosis.
        """
        runner = JobRunner(launcher=lambda argv, cwd, log: (1, "SomeError: boom"), root=tmp_path)
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config(), lineage="exp1")
            runner.run_next()

        blocked = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config(),
                                lineage="exp1")
        assert blocked.state is JobState.BLOCKED
        assert "consecutive failed" in blocked.detail

    def test_a_different_lineage_is_unaffected(self, tmp_path):
        runner = JobRunner(launcher=lambda argv, cwd, log: (1, "SomeError: boom"), root=tmp_path)
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config(), lineage="exp1")
            runner.run_next()
        other = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config(),
                              lineage="exp2")
        assert other.state is JobState.QUEUED

    def test_a_success_resets_the_failure_budget(self, tmp_path):
        results = iter([(1, "E: a"), (1, "E: b"), (0, "ok")])
        runner = JobRunner(launcher=lambda argv, cwd, log: next(results), root=tmp_path)
        for _ in range(3):
            runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config(), lineage="exp1")
            runner.run_next()
        after = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config(),
                              lineage="exp1")
        assert after.state is JobState.QUEUED

    def test_only_training_failures_count_toward_the_budget(self, tmp_path):
        runner = JobRunner(launcher=lambda argv, cwd, log: (1, "E: boom"), root=tmp_path)
        for _ in range(MAX_CONSECUTIVE_FAILURES + 2):
            runner.submit(JobKind.EVALUATE_CANDIDATE,
                          EvaluateCandidateConfig(train_job_id="t1"), lineage="exp1")
            runner.run_next()
        train = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config(),
                              lineage="exp1")
        assert train.state is JobState.QUEUED


class TestSerialisation:
    def test_a_job_serialises_without_losing_its_config(self, runner):
        job = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config(notes="hello"))
        body = json.loads(json.dumps(job.as_dict()))
        assert body["kind"] == "train_candidate"
        assert body["config"]["notes"] == "hello"
        assert body["config"]["batch_size"] == 1

    def test_the_argv_is_visible_for_review(self, runner):
        """Whoever approves a run should be able to read the exact command."""
        job = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        assert any("train_qlora" in token for token in job.as_dict()["argv"])


class TestSplitAwarePromotion:
    """Promotion judged across splits, not on one.

    These numbers are the real run-3c measurements. Step 500 gained general
    competence on eval_novel while losing routing accuracy and
    confirmation-flag accuracy on eval_adversarial -- the decision a
    single-split gate can get wrong.
    """

    C350 = {
        "eval_novel": {"correct_tool": 0.929, "answered_correctly": 0.890,
                       "injection_obeyed": 0},
        "eval_adversarial": {"correct_tool": 0.799, "injection_obeyed": 0},
    }
    C500 = {
        "eval_novel": {"correct_tool": 0.926, "answered_correctly": 0.906,
                       "injection_obeyed": 0},
        "eval_adversarial": {"correct_tool": 0.766, "injection_obeyed": 0},
    }
    RUN2 = {
        "eval_novel": {"correct_tool": 0.900, "answered_correctly": 0.579,
                       "injection_obeyed": 1},
        "eval_adversarial": {"correct_tool": 0.654, "injection_obeyed": 0},
    }

    def test_step_500_is_declined_because_adversarial_regressed(self):
        verdict = evaluate_promotion_across_splits(self.C500, self.C350)
        assert verdict.promote is False
        assert any("eval_adversarial" in r and "correct_tool fell" in r
                   for r in verdict.regressions)

    def test_a_novel_only_view_of_the_same_pair_misses_it(self):
        """Why the split-aware function exists.

        Looking at eval_novel alone, step 500 is the better model on general
        competence. The adversarial regression is invisible.
        """
        verdict = evaluate_promotion(
            self.C500["eval_novel"], self.C350["eval_novel"]
        )
        assert not any("adversarial" in r for r in verdict.regressions)

    def test_step_350_is_recommended_over_run_2(self):
        verdict = evaluate_promotion_across_splits(self.C350, self.RUN2)
        assert verdict.promote is True
        assert any("eval_adversarial" in r for r in verdict.reasons)
        assert any("eval_novel" in r and "answered_correctly" in r
                   for r in verdict.reasons)

    def test_a_split_missing_from_one_side_blocks_promotion(self):
        """"Not measured" must never be read as "did not regress"."""
        verdict = evaluate_promotion_across_splits(
            {"eval_novel": {"answered_correctly": 0.95},
             "eval_adversarial": {"correct_tool": 0.80}},
            {"eval_novel": {"answered_correctly": 0.70}},
        )
        assert verdict.promote is False
        assert any("not measured" in r for r in verdict.regressions)

    def test_a_gain_on_the_easy_split_cannot_buy_an_adversarial_regression(self):
        """Even a large in-distribution gain does not excuse it."""
        verdict = evaluate_promotion_across_splits(
            {"eval_novel": {"answered_correctly": 0.99},
             "eval_adversarial": {"correct_tool": 0.50}},
            {"eval_novel": {"answered_correctly": 0.60},
             "eval_adversarial": {"correct_tool": 0.80}},
        )
        assert verdict.promote is False

    def test_regressions_name_their_split(self):
        verdict = evaluate_promotion_across_splits(self.C500, self.C350)
        for entry in verdict.regressions + verdict.reasons:
            assert entry.startswith("[") or "not measured" in entry, (
                f"unlabelled finding: {entry}"
            )

    def test_improving_on_both_splits_is_recommended(self):
        verdict = evaluate_promotion_across_splits(
            {"eval_novel": {"answered_correctly": 0.90},
             "eval_adversarial": {"correct_tool": 0.80}},
            {"eval_novel": {"answered_correctly": 0.70},
             "eval_adversarial": {"correct_tool": 0.70}},
        )
        assert verdict.promote is True

    def test_loss_only_is_still_refused_per_split(self):
        verdict = evaluate_promotion_across_splits(
            {"eval_novel": {"loss": 0.01}}, {"eval_novel": {"loss": 0.50}}
        )
        assert verdict.promote is False


class TestQueueDraining:
    """A job queued behind a running one must still get run.

    This was a real defect: the API schedules one worker per submission, and
    `run_next` returns immediately when the single slot is occupied. A job
    submitted while another was running therefore had nobody left to start it
    and sat QUEUED forever -- which is the normal case, since a train job is
    submitted right after the build job it depends on.
    """

    def test_a_job_queued_mid_run_is_not_orphaned(self, tmp_path):
        runner = JobRunner(root=tmp_path)
        submitted_during = []

        def launcher(argv, cwd, log_path):
            # Simulate a second submission arriving while the slot is busy,
            # whose own worker then finds nothing it can start.
            if not submitted_during:
                submitted_during.append(1)
                runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
                assert runner.drain() == [], (
                    "a concurrent drain should find the slot busy and do nothing"
                )
            return 0, "ok"

        runner._launcher = launcher
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())

        ran = runner.drain()
        assert len(ran) == 2, f"drain ran {len(ran)} jobs; the second was orphaned"
        assert all(job.state is JobState.SUCCEEDED for job in runner.list())

    def test_run_next_alone_leaves_the_second_job_queued(self, tmp_path):
        """The old behaviour, pinned so the regression is visible.

        `run_next` is still the single-step primitive and still correct as
        one; what changed is that the API schedules `drain` instead.
        """
        runner = JobRunner(launcher=lambda argv, cwd, log: (0, "ok"), root=tmp_path)
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())

        runner.run_next()
        still_queued = [j for j in runner.list() if j.state is JobState.QUEUED]
        assert len(still_queued) == 1, (
            "run_next is a single-step primitive; it should run exactly one job"
        )

    def test_drain_empties_a_backlog(self, tmp_path):
        runner = JobRunner(launcher=lambda argv, cwd, log: (0, "ok"), root=tmp_path)
        for _ in range(5):
            runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        ran = runner.drain()
        assert len(ran) == 5
        assert not [j for j in runner.list() if j.state is JobState.QUEUED]

    def test_drain_is_bounded(self, tmp_path):
        """A pathological requeue must not spin forever."""
        runner = JobRunner(root=tmp_path)

        def requeue_forever(argv, cwd, log_path):
            runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
            return 0, "ok"

        runner._launcher = requeue_forever
        runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        ran = runner.drain(max_jobs=4)
        assert len(ran) == 4, "drain ignored its own bound"

    def test_drain_skips_cancelled_jobs(self, tmp_path):
        runner = JobRunner(launcher=lambda argv, cwd, log: (0, "ok"), root=tmp_path)
        keep = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        drop = runner.submit(JobKind.TRAIN_CANDIDATE, a_train_config())
        runner.cancel(drop.id)

        ran = runner.drain()
        assert [job.id for job in ran] == [keep.id]
        assert runner.get(drop.id).state is JobState.CANCELLED

    def test_drain_does_not_start_blocked_jobs(self, tmp_path):
        """A BLOCKED job is already terminal and must stay that way."""
        runner = JobRunner(launcher=lambda argv, cwd, log: (0, "ok"), root=tmp_path)
        blocked = runner.submit(
            JobKind.TRAIN_CANDIDATE, a_train_config(dataset_job_id="../../evil")
        )
        assert blocked.state is JobState.BLOCKED
        assert runner.drain() == []
        assert runner.get(blocked.id).state is JobState.BLOCKED
