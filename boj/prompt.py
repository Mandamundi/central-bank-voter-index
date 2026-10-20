"""Bank of Japan wording and appendix for the shared rubric (rubric.py).

The appendix states facts about the Bank (who votes, its instruments, the language of its texts);
it never says which level to give. Changing anything here that affects scores requires bumping
rubric.VERSION in all three projects.
"""
from __future__ import annotations

import rubric
from rubric import LEVELS, level_to_value  # noqa: F401  (used by build_index and tests)

APPENDIX = """Bank of Japan notes:
- Voters are the Governor, the two Deputy Governors and the six other Policy Board members. A speech
  by the Governor read out by a Deputy Governor (代読) is the Governor's speech.
- Instruments: the short-term policy rate (the uncollateralized overnight call rate, or a negative
  rate applied to part of current-account balances); purchases of JGBs, which may include a target for
  the 10-year JGB yield (yield curve control); and purchases of ETFs and J-REITs. The policy setting
  below states which are in use on the speech date. Funds-supplying operations for lending support are
  lending schemes for banks.
- Texts are mostly Japanese originals, some English originals. Copy evidence quotes exactly in the
  language of the text; never translate them.

"""

BANK = rubric.Bank(
    analyst="a senior Bank of Japan analyst",
    official="a Bank of Japan Policy Board member",
    policy_area="Japanese monetary policy",
    appendix=APPENDIX,
)

SpeechScore = rubric.score_model(BANK.policy_area)


def system_prompt() -> str:
    return rubric.system_prompt(BANK)


def user_prompt(speech: dict, setting: str) -> str:
    return rubric.user_prompt(speech, setting)
