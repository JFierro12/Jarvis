"""Tests for the candidate-dataset builder.

Mostly refusals again. The builder's job is to be the last thing that can stop
a bad corpus reaching a four-hour training run, so the valuable behaviour is
the non-zero exit.
"""

import json

import pytest

from jarvis_train.build_candidate_dataset import (
    dedupe,
    main,
    normalise_row,
    validate_row,
)
from jarvis_train.contract import TUNED_SYSTEM_PROMPT


def answer(spoken="Four, sir.", call=None, confirm=False):
    return json.dumps({
        "spoken_answer": spoken,
        "requires_confirmation": confirm,
        "proposed_tool_call": call,
    })


def row(prompt="What is 2+2?", expected=None, system="SYS"):
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer() if expected is None else expected},
        ]
    }


def write_jsonl(path, rows):
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return path


class TestValidateRow:
    def test_a_good_row_passes(self):
        assert validate_row(row()) == (True, "")

    def test_prose_as_the_assistant_message_fails(self):
        ok, why = validate_row(row(expected="Four, sir."))
        assert not ok and "not valid JSON" in why

    def test_a_missing_system_prompt_fails(self):
        bad = {"messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": answer()},
        ]}
        ok, why = validate_row(bad)
        assert not ok and "system prompt" in why

    def test_an_unregistered_tool_fails(self):
        call = {"tool_name": "launch_missiles", "target": "", "content": "",
                "arguments": {}}
        ok, why = validate_row(row(expected=answer(call=call)))
        assert not ok and "not a registered tool" in why

    def test_a_registered_tool_passes(self):
        call = {"tool_name": "get_current_time", "target": "", "content": "",
                "arguments": {}}
        assert validate_row(row(expected=answer(call=call)))[0]

    def test_populated_arguments_fail(self):
        call = {"tool_name": "get_current_time", "target": "", "content": "",
                "arguments": {"tz": "UTC"}}
        ok, why = validate_row(row(expected=answer(call=call)))
        assert not ok and "empty object" in why

    def test_a_confirmation_tool_without_the_flag_fails(self):
        """A row proposing a destructive tool while claiming no confirmation
        is needed teaches the model to bypass the confirmation boundary.

        The registry decides which tools need confirmation, so the check is
        against the registry rather than a list restated here.
        """
        call = {"tool_name": "shutdown_pc", "target": "", "content": "",
                "arguments": {}}
        ok, why = validate_row(row(expected=answer(call=call, confirm=False)))
        assert not ok and "requires confirmation" in why

    def test_a_confirmation_tool_with_the_flag_passes(self):
        call = {"tool_name": "shutdown_pc", "target": "", "content": "",
                "arguments": {}}
        assert validate_row(row(expected=answer(call=call, confirm=True)))[0]


class TestNormalise:
    def test_a_stale_system_prompt_is_rewritten(self):
        """An example collected under an older prompt must not train the model
        on a prompt the application no longer sends."""
        normalised = normalise_row(row(system="OLD PROMPT"), TUNED_SYSTEM_PROMPT)
        assert normalised["messages"][0]["content"] == TUNED_SYSTEM_PROMPT

    def test_the_user_and_assistant_text_survive(self):
        normalised = normalise_row(row(prompt="unique question"), "SYS")
        assert normalised["messages"][1]["content"] == "unique question"
        assert "Four, sir." in normalised["messages"][2]["content"]


class TestDedupe:
    def test_a_repeated_prompt_is_dropped(self):
        kept, dropped = dedupe([row(prompt="same"), row(prompt="SAME")])
        assert len(kept) == 1 and dropped == 1

    def test_the_first_occurrence_wins(self):
        """Base rows are passed first, so a curated row is kept over a later
        conflicting correction. A conflict is a review question, not something
        to settle by training on both."""
        first = row(prompt="q", expected=answer("Base answer."))
        second = row(prompt="q", expected=answer("Correction answer."))
        kept, _ = dedupe([first, second])
        assert "Base answer." in kept[0]["messages"][2]["content"]


class TestBuildRefusals:
    def test_a_corrections_only_build_is_refused_by_default(self, tmp_path):
        """Fine-tuning on only the cases the model got wrong causes forgetting.

        Corrections are by construction an unrepresentative sample of the
        input distribution.
        """
        approved = write_jsonl(tmp_path / "approved.jsonl", [row()])
        code = main([
            "--approved", str(approved),
            "--out", str(tmp_path / "train.jsonl"),
            "--min-examples", "1",
        ])
        assert code == 2
        assert not (tmp_path / "train.jsonl").exists()

    def test_an_explicit_flag_permits_it(self, tmp_path):
        approved = write_jsonl(tmp_path / "approved.jsonl", [row()])
        code = main([
            "--approved", str(approved),
            "--out", str(tmp_path / "train.jsonl"),
            "--min-examples", "1",
            "--allow-corrections-only",
        ])
        assert code == 0
        assert (tmp_path / "train.jsonl").exists()

    def test_too_few_rows_refuses_to_write(self, tmp_path):
        """A candidate trained on a handful of rows cannot be evaluated with
        enough power to tell improvement from noise."""
        approved = write_jsonl(tmp_path / "approved.jsonl", [row()])
        code = main([
            "--approved", str(approved),
            "--out", str(tmp_path / "train.jsonl"),
            "--min-examples", "200",
            "--allow-corrections-only",
        ])
        assert code == 3
        assert not (tmp_path / "train.jsonl").exists()

    def test_a_missing_approved_file_is_an_error(self, tmp_path):
        code = main([
            "--approved", str(tmp_path / "nope.jsonl"),
            "--out", str(tmp_path / "train.jsonl"),
        ])
        assert code == 2

    def test_a_missing_base_corpus_is_an_error(self, tmp_path):
        approved = write_jsonl(tmp_path / "approved.jsonl", [row()])
        code = main([
            "--approved", str(approved),
            "--out", str(tmp_path / "train.jsonl"),
            "--base", str(tmp_path / "absent.jsonl"),
        ])
        assert code == 2

    def test_malformed_jsonl_fails_loudly(self, tmp_path):
        bad = tmp_path / "approved.jsonl"
        bad.write_text('{"messages": [}\n', encoding="utf-8")
        with pytest.raises(SystemExit, match="not valid JSON"):
            main([
                "--approved", str(bad),
                "--out", str(tmp_path / "train.jsonl"),
                "--allow-corrections-only",
            ])


class TestBuildSucceeds:
    def test_invalid_rows_are_dropped_and_counted(self, tmp_path):
        rows = [row(prompt=f"q{i}") for i in range(5)]
        rows.append(row(prompt="bad", expected="not json"))
        approved = write_jsonl(tmp_path / "approved.jsonl", rows)
        out = tmp_path / "train.jsonl"

        code = main([
            "--approved", str(approved), "--out", str(out),
            "--min-examples", "5", "--allow-corrections-only",
        ])
        assert code == 0

        written = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert len(written) == 5

        report = json.loads((tmp_path / "dataset_report.json").read_text(encoding="utf-8"))
        assert report["usable_rows"] == 5
        assert report["approved_examples"] == 6
        assert sum(report["rejections"].values()) == 1

    def test_the_base_corpus_is_mixed_in(self, tmp_path):
        base = write_jsonl(tmp_path / "base.jsonl",
                           [row(prompt=f"base{i}") for i in range(10)])
        approved = write_jsonl(tmp_path / "approved.jsonl",
                               [row(prompt=f"new{i}") for i in range(3)])
        out = tmp_path / "train.jsonl"

        code = main([
            "--approved", str(approved), "--out", str(out),
            "--base", str(base), "--min-examples", "13",
        ])
        assert code == 0
        written = out.read_text(encoding="utf-8").splitlines()
        assert len(written) == 13

        report = json.loads((tmp_path / "dataset_report.json").read_text(encoding="utf-8"))
        assert report["base_rows"] == 10
        assert report["corrections_only"] is False

    def test_every_written_row_uses_the_current_system_prompt(self, tmp_path):
        approved = write_jsonl(tmp_path / "approved.jsonl",
                               [row(prompt=f"q{i}", system="STALE") for i in range(3)])
        out = tmp_path / "train.jsonl"
        main([
            "--approved", str(approved), "--out", str(out),
            "--min-examples", "1", "--allow-corrections-only",
        ])
        for line in out.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["messages"][0]["content"] == TUNED_SYSTEM_PROMPT

    def test_the_report_fingerprints_the_prompt(self, tmp_path):
        """So a later comparison can tell whether two candidates were trained
        against the same prompt."""
        approved = write_jsonl(tmp_path / "approved.jsonl", [row()])
        main([
            "--approved", str(approved), "--out", str(tmp_path / "train.jsonl"),
            "--min-examples", "1", "--allow-corrections-only",
        ])
        report = json.loads((tmp_path / "dataset_report.json").read_text(encoding="utf-8"))
        assert len(report["system_prompt_sha"]) == 16
