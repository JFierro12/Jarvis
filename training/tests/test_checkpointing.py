"""Tests for resumable checkpointing.

These exist because of a concrete loss: run 3 stalled at step 230 of 500 after
six and a half hours of GPU time, and the only artifact that survived was a
single rolling adapter directory holding step 200. Weights alone could not
resume -- no optimizer moments, no step counter, no LR-schedule position -- so
the run had to start over from zero.

Everything here runs on CPU with no model loaded. The parts that genuinely
need a GPU (that a resumed run's loss continues the curve rather than jumping)
are exercised by the training smoke test, not here.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_train.train_qlora import (  # noqa: E402
    _checkpoint_dirs,
    _prune_checkpoints,
)


def make_checkpoint(out: Path, step: int, *, with_state: bool = True) -> Path:
    path = out / f"checkpoint-{step:05d}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "adapter_model.safetensors").write_bytes(b"\x00" * 16)
    (path / "adapter_config.json").write_text("{}", encoding="utf-8")
    if with_state:
        (path / "training_state.pt").write_bytes(b"\x00" * 8)
    return path


class TestCheckpointDiscovery:
    def test_orders_by_step_not_lexicographically(self, tmp_path):
        """`checkpoint-1000` sorts before `checkpoint-200` as a string.

        Zero-padding makes the two orderings agree, but the parser must not
        depend on that -- pruning by the wrong order deletes the newest
        checkpoint and keeps the oldest.
        """
        for step in (50, 200, 1000, 400):
            make_checkpoint(tmp_path, step)
        steps = [int(p.name.split("-")[-1]) for p in _checkpoint_dirs(tmp_path)]
        assert steps == [50, 200, 400, 1000]

    def test_ignores_unrelated_directories_and_files(self, tmp_path):
        make_checkpoint(tmp_path, 100)
        (tmp_path / "checkpoint-notanumber").mkdir()
        (tmp_path / "adapter_model.safetensors").write_bytes(b"\x00")
        (tmp_path / "checkpoint-999.txt").write_text("x", encoding="utf-8")
        assert [p.name for p in _checkpoint_dirs(tmp_path)] == ["checkpoint-00100"]

    def test_empty_output_dir_has_no_checkpoints(self, tmp_path):
        assert _checkpoint_dirs(tmp_path) == []


class TestPruning:
    def test_keeps_the_newest_n(self, tmp_path):
        for step in (100, 200, 300, 400, 500):
            make_checkpoint(tmp_path, step)
        _prune_checkpoints(tmp_path, keep=2)
        steps = [int(p.name.split("-")[-1]) for p in _checkpoint_dirs(tmp_path)]
        assert steps == [400, 500]

    def test_keep_zero_means_keep_everything(self, tmp_path):
        """0 is 'unbounded', not 'delete them all'.

        Getting this backwards would silently destroy every checkpoint of a
        long run at its first save.
        """
        for step in (100, 200, 300):
            make_checkpoint(tmp_path, step)
        _prune_checkpoints(tmp_path, keep=0)
        assert len(_checkpoint_dirs(tmp_path)) == 3

    def test_negative_keep_is_also_a_no_op(self, tmp_path):
        make_checkpoint(tmp_path, 100)
        _prune_checkpoints(tmp_path, keep=-1)
        assert len(_checkpoint_dirs(tmp_path)) == 1

    def test_pruning_fewer_than_keep_removes_nothing(self, tmp_path):
        make_checkpoint(tmp_path, 100)
        _prune_checkpoints(tmp_path, keep=5)
        assert len(_checkpoint_dirs(tmp_path)) == 1

    def test_pruning_removes_the_whole_directory(self, tmp_path):
        old = make_checkpoint(tmp_path, 100)
        make_checkpoint(tmp_path, 200)
        _prune_checkpoints(tmp_path, keep=1)
        assert not old.exists()


class TestResumeRefusesIncompleteCheckpoints:
    def test_weights_without_optimizer_state_are_refused_loudly(self, tmp_path):
        """The exact shape of tonight's loss.

        `out/adapter-3b-v3-step200` holds adapter weights and nothing else.
        Loading those and calling it a resume would restart Adam's moments
        from zero and put the cosine LR schedule back at step 0 -- producing a
        run that is not the one it claims to continue. Refusing is the point;
        a warning would be read as a formality and ignored.
        """
        from jarvis_train.train_qlora import _load_checkpoint

        path = make_checkpoint(tmp_path, 200, with_state=False)
        with pytest.raises(SystemExit) as exc:
            _load_checkpoint(path, model=None, optimizer=None)

        message = str(exc.value)
        assert "training_state.pt" in message
        # The message has to explain the consequence, not just the absence.
        assert "optimizer" in message.lower()
        assert "schedule" in message.lower()

    def test_real_run3_partial_checkpoint_is_correctly_identified_as_incomplete(self):
        """Against the actual artifact, not a synthetic one."""
        from jarvis_train.train_qlora import _load_checkpoint

        real = Path(__file__).resolve().parents[1] / "out" / "adapter-3b-v3-step200"
        if not real.exists():
            pytest.skip("run-3 partial checkpoint not present")
        assert (real / "adapter_model.safetensors").exists()
        assert not (real / "training_state.pt").exists()
        with pytest.raises(SystemExit):
            _load_checkpoint(real, model=None, optimizer=None)


class TestRngStateCoercion:
    def test_byte_tensor_coercion_accepts_what_torch_save_returns(self):
        torch = pytest.importorskip("torch")
        from jarvis_train.train_qlora import _as_byte_tensor

        original = torch.get_rng_state()
        assert _as_byte_tensor(original).dtype == torch.uint8
        # A list round-tripped through a non-torch serializer must still work.
        assert _as_byte_tensor([1, 2, 3]).dtype == torch.uint8

    def test_restored_state_reproduces_the_same_draws(self):
        torch = pytest.importorskip("torch")
        from jarvis_train.train_qlora import _as_byte_tensor

        saved = torch.get_rng_state()
        first = torch.randn(4)
        torch.set_rng_state(_as_byte_tensor(saved))
        second = torch.randn(4)
        assert torch.equal(first, second), "RNG restore did not reproduce the draw"


class TestCheckpointArgsAreRecorded:
    def test_saved_state_records_the_settings_that_define_the_run(self, tmp_path):
        """A checkpoint resumed under different hyperparameters is a new
        experiment. Recording them is what makes that detectable later."""
        import inspect

        from jarvis_train import train_qlora

        source = inspect.getsource(train_qlora._save_checkpoint)
        for field in ("lr", "grad_accum", "batch_size", "max_len", "seed"):
            assert f'"{field}"' in source, f"{field} is not recorded in the checkpoint"

    def test_state_file_is_written_atomically(self):
        """A half-written state file must not look like a complete checkpoint;
        a crash during a save would otherwise poison the resume path."""
        import inspect

        from jarvis_train import train_qlora

        source = inspect.getsource(train_qlora._save_checkpoint)
        assert ".tmp" in source and "replace(" in source
