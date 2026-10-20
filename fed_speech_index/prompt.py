"""Fed wording for the shared rubric (rubric.py). The Fed needs no appendix.

tests/test_prompt.py pins the rendered prompt.
Changing anything here that affects scores requires bumping rubric.VERSION.
"""
from __future__ import annotations

import rubric
from rubric import LEVELS, level_to_value  # noqa: F401  (used by build_index and tests)

BANK = rubric.Bank(
    analyst="a senior Federal Reserve analyst",
    official="a Federal Reserve official",
    policy_area="U.S. monetary policy",
)

SpeechScore = rubric.score_model(BANK.policy_area)


def system_prompt() -> str:
    return rubric.system_prompt(BANK)


def user_prompt(speech: dict, fed_funds_level: str) -> str:
    unit = "target range" if "-" in fed_funds_level else "target rate"
    return rubric.user_prompt(speech, f"federal funds {unit} {fed_funds_level}%.")
