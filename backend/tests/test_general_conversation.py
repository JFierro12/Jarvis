"""Regression guards for the general-conversation collapse.

The failure these exist to prevent was not a model defect. The app serves
`llama3.1:8b-instruct-q4_K_M` (untuned) with the `production` prompt, and that
prompt described nothing but tool routing, safety rules and a single
hand-carved football exception. With no instruction authorizing ordinary
knowledge, the model inferred that anything not matching a tool was out of
scope. Measured, provider-direct, before the fix:

    "Who wrote 1984?"                       -> "George Orwell wrote 1984."
    same question, after an earlier turn
    falsely claiming narrow knowledge       -> "I'm not aware of any tool that
                                                can provide information about
                                                the author of a book, sir."
    "What knowledge do you have available?" -> "I have knowledge of available
                                                tools, system policies, and
                                                prior conversation history."

These are text-level assertions on the instructions, in the same spirit as
test_local_reasoning.py's trust-boundary test: they cannot prove the model
behaves, but they fail loudly if the instructions that produced the measured
fix are weakened or deleted. Behavioural measurement lives in
`scripts/probe_conversation.py`, which calls the real model.
"""

import re

import pytest

from app.core.config import Settings
from app.services.anthropic_reasoning import SYSTEM_PROMPT, TUNED_SYSTEM_PROMPT


def flat(text):
    """Collapse hard-wrap newlines so a phrase assertion does not depend
    on where the prompt happens to break lines."""
    return re.sub(r"\s+", " ", text).lower()


BOTH = pytest.mark.parametrize("prompt", [SYSTEM_PROMPT, TUNED_SYSTEM_PROMPT],
                               ids=["production", "tuned"])


@BOTH
def test_tools_are_described_as_executable_scope_not_knowledge_scope(prompt):
    """The specific inference that broke it: available_tools read as the edge
    of what the model knows."""
    lowered = flat(prompt)
    assert "not the limits of what you know" in lowered or \
           "says nothing" in lowered, "prompt must separate executing from knowing"
    assert "from your own knowledge" in lowered or "answer from what you know" in lowered


@BOTH
def test_general_questions_are_answered_without_a_tool(prompt):
    lowered = flat(prompt)
    assert "no tool" in lowered
    assert "refus" in lowered  # must say not to refuse these


@BOTH
def test_unverifiable_information_is_named_rather_than_invented(prompt):
    lowered = flat(prompt)
    assert "never invent" in lowered
    for thing in ("search", "citation"):
        assert thing in lowered


@BOTH
def test_prior_assistant_claims_do_not_redefine_capability(prompt):
    """History stays useful evidence, but a previous turn's false modesty is
    not authoritative -- this is what case E1 regressed on."""
    lowered = flat(prompt)
    assert "earlier assistant turn" in lowered or "previous turn" in lowered
    assert "was wrong" in lowered or "was false" in lowered


@BOTH
def test_explaining_training_is_distinguished_from_performing_it(prompt):
    lowered = flat(prompt)
    assert "does not retrain you" in lowered
    assert "is not doing it" in lowered or "is not performing it" in lowered


@BOTH
def test_self_description_may_not_be_fabricated(prompt):
    lowered = flat(prompt)
    assert "cutoff" in lowered
    assert "can be wrong" in lowered or "factually wrong" in lowered


@BOTH
def test_depth_is_allowed_and_also_bounded(prompt):
    """Both directions matter. Without the allowance, explanations collapse to
    one sentence; without the bound, 'explain in full depth the limits of your
    knowledge' made the 8B emit the same two sentences three times."""
    lowered = flat(prompt)
    assert "explain" in lowered and "code" in lowered
    assert "brevity is the default" in lowered or "not concision" in lowered


def test_production_prompt_distinguishes_the_four_learning_mechanisms():
    lowered = flat(SYSTEM_PROMPT)
    assert "memory tools" in lowered
    assert "retrieval tool" in lowered
    assert "weights" in lowered
    assert "external training pipeline" in lowered
    # The specific wrong answer to rule out: that a missing implementation is
    # merely a policy/permission setting.
    assert "permissions or policy setting" in lowered


def test_production_prompt_still_bans_claiming_absent_sensors_and_accounts():
    """The hardcoded behavioural rule from AGENTS.md must survive this edit."""
    lowered = flat(SYSTEM_PROMPT)
    assert "never claim to be continuously watching" in lowered
    assert "never claim access to a sensor or account you do not have" in lowered


# --- Do not teach to the evaluation --------------------------------------
#
# The agreed behavioural checks include explaining Dijkstra's algorithm with
# its non-negative-edge requirement. If that answer is written into the prompt,
# the check stops measuring generalisation and starts measuring copying. The
# prompt states the general rule ("state the conditions under which it is
# actually correct") and must never name the probe's subjects.

PROBE_SUBJECTS = ["dijkstra", "astroworld", "travis scott", "orwell", "1984",
                  "non-negative edge", "nonnegative edge", "brave new world",
                  "huxley", "2:40", "25 minutes", "binary search", "hash table",
                  # Subjects of the deferral probe (scripts/probe_conversation.py
                  # CURRENT family and the user-reported cases). Naming any of
                  # them here would turn that probe from a generalisation
                  # measurement into a copying measurement.
                  "grand theft auto", "gta", "age of ultron", "ultron",
                  "rockstar", "artemis", "bitcoin", "marvel", "iphone",
                  "zelda", "premier league", "formula 1", "james webb",
                  "starship", "elder scrolls"]


@BOTH
def test_no_probe_answer_is_hardcoded_into_the_prompt(prompt):
    lowered = flat(prompt)
    leaked = [s for s in PROBE_SUBJECTS if s in lowered]
    assert not leaked, f"probe answers leaked into the prompt: {leaked}"


def test_the_general_rule_replacing_them_is_present():
    """Production only -- the distilled prompt drops this level of guidance by
    design, the same way it drops target formats."""
    assert "conditions under which it is actually correct" in flat(SYSTEM_PROMPT)


# --- Conversation versioning tracks the prompt, not a hand-typed string ---


def _version(**kwargs):
    from app.api.runtime import prompt_version
    return prompt_version(Settings(local_llm_base_url="http://x", local_llm_model="m", **kwargs))


def test_switching_prompt_profile_changes_the_conversation_version():
    assert _version(local_llm_prompt_profile="production") != _version(local_llm_prompt_profile="tuned")


def test_unrecognized_profile_versions_as_production():
    """Must match LocalLanguageReasoningProvider's fail-closed fallback, or a
    typo would read as a configuration change and 409 every conversation."""
    assert _version(local_llm_prompt_profile="typo") == _version(local_llm_prompt_profile="production")


def test_version_is_derived_from_the_prompt_body():
    """The old constant was a literal, so editing SYSTEM_PROMPT left existing
    conversations running under instructions they were never created with."""
    import app.api.runtime as runtime
    baseline = _version(local_llm_prompt_profile="production")
    original = runtime.SYSTEM_PROMPT
    try:
        runtime.SYSTEM_PROMPT = original + "\n\nan added instruction\n"
        assert _version(local_llm_prompt_profile="production") != baseline
    finally:
        runtime.SYSTEM_PROMPT = original
    assert _version(local_llm_prompt_profile="production") == baseline


# --- Answering versus offering to answer ---------------------------------
#
# The second-generation form of the collapse documented at the top of this
# file. Once the prompt authorised general knowledge, the model stopped
# refusing and started *deferring*: describing the answer it could give and
# asking whether the user wanted to hear it, instead of giving it. It reads as
# politeness and costs the user the entire turn. Measured provider-direct on
# the 45-item probe set, before the block these tests guard:
#
#   a question about a film character's fate -> "I can provide general
#       information about [the character]'s role ... If you'd like, I can try
#       to provide a general summary."  -- and never stated the fate.
#   a question about recent news on a game   -> "... Would you like me to
#       provide some general information on [the game]?"
#
# Deferral ran 13/42 on the A+B families before and 3/42 after; current-
# information answers scored good 4/18 before and 14/18 after; settled-
# knowledge answers went 21/24 to 24/24. Production prompt only -- the tuned
# prompt drops this guidance by design, as it drops the other prose rules.
#
# One earlier attempt made it WORSE and is recorded so it is not retried: a
# block that quoted the offending phrases verbatim as forbidden examples drove
# the 8B to emit those exact strings as its whole answer, taking current-
# information behaviour from 2/6 to 0/6. State the rule positively; do not
# hand a small model the bad phrasing to copy.


def test_prompt_requires_giving_the_answer_not_offering_it():
    lowered = flat(SYSTEM_PROMPT)
    assert "only characterised an answer" in lowered
    assert "never instead of it" in lowered


def test_prompt_requires_labelled_stale_knowledge_rather_than_withholding():
    """Uncertainty must caveat an answer, not replace one."""
    lowered = flat(SYSTEM_PROMPT)
    assert "never a reason to withhold one" in lowered
    assert "end date rather than as the present state" in lowered


def test_prompt_gives_the_three_part_shape_for_recent_questions():
    """Naming the live-data limit alone was the failure; substance must follow
    it, which is why the prompt says so explicitly."""
    lowered = flat(SYSTEM_PROMPT)
    assert "cannot check live sources" in lowered
    assert "stopping after the first part fails the user" in lowered


def test_self_description_is_scoped_to_questions_about_itself():
    """The self-description leaked into answers about the world in 11 of 42
    probe items. Scoping it cut that to 2."""
    lowered = flat(SYSTEM_PROMPT)
    assert "only in answers to questions about yourself" in lowered
    assert "the first sentence of your answer is about that subject" in lowered


def test_anti_deferral_block_quotes_no_offending_phrase():
    """Guards the regression that the failed attempt caused: quoting the bad
    phrasing teaches a small model to emit it."""
    lowered = flat(SYSTEM_PROMPT)
    for phrase in ("i can provide general information",
                   "would you like me to",
                   "if you'd like, i can"):
        assert phrase not in lowered, (
            f"{phrase!r} appears in the prompt; quoting the deferral phrasing "
            "is what made the 8B emit it verbatim"
        )
