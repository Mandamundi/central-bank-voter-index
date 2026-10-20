"""Bank of England wording and appendix for the shared rubric (rubric.py).

The appendix states facts about the Bank (who votes, its instruments);
it never says which level to give. Changing anything here that affects scores requires bumping
rubric.VERSION in all three projects.
"""
from __future__ import annotations

import rubric
from rubric import LEVELS, level_to_value  # noqa: F401  (used by build_index and tests)

APPENDIX = """Bank of England notes:
- Voters are the nine members of the Monetary Policy Committee (MPC): the Governor, three Deputy
  Governors, the Chief Economist and four external members. Views given only in a Financial Policy
  Committee or Prudential Regulation Committee capacity are not monetary policy views.
- Instruments: Bank Rate is the policy rate. Asset purchases are purchases of gilts (and corporate
  bonds) held in the Asset Purchase Facility (APF); quantitative tightening (QT) reduces the APF through
  maturities and gilt sales. The Term Funding Schemes are lending schemes for banks. The Bank has also
  bought gilts as a temporary financial stability operation.

"""

BANK = rubric.Bank(
    analyst="a senior Bank of England analyst",
    official="a Bank of England Monetary Policy Committee member",
    policy_area="UK monetary policy",
    appendix=APPENDIX,
)

SpeechScore = rubric.score_model(BANK.policy_area)


def system_prompt() -> str:
    return rubric.system_prompt(BANK)


def user_prompt(speech: dict, setting: str) -> str:
    return rubric.user_prompt(speech, setting)
