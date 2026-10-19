"""Shared scoring rubric: the absolute 7-level policy-stance scale, the prompt core and the response schema.

This file is identical in the Fed, BOJ and BOE projects, so every bank's speeches are scored with the
same instrument. Each project's prompt.py fills in the bank's names and adds a short appendix of facts
about the bank (who votes, its policy instruments); the appendix never says which level to give.
tests/test_rubric.py pins this file's checksum: after an edit, copy it to all three projects, update
the checksum in all three tests and bump VERSION (which starts a new score vintage everywhere).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from pydantic import BaseModel, Field

VERSION = "v4"
MAX_TEXT_CHARS = 150_000

# level -> (0-100 value, definition). The scale is ABSOLUTE: it measures how easy or tight
# the policy the speaker supports is, on one fixed yardstick across all eras.
LEVELS = {
    1: (0.0, "EMERGENCY EASING. Supports cutting rates rapidly to or near zero and/or launching or "
             "expanding large-scale asset purchases or emergency facilities; crisis framing, "
             "'use all our tools'."),
    2: (100 / 6, "MAXIMUM ACCOMMODATION HELD, OR LARGE EASING. Supports keeping rates at or near zero "
                 "with ongoing asset purchases, or promises rates will stay very low for a long time; "
                 "tightening is not in view. Also: supports large or repeated cuts to well below "
                 "neutral in a downturn."),
    3: (200 / 6, "ACCOMMODATIVE BUT PREPARING TO REMOVE SUPPORT. Policy is still clearly accommodative, "
                 "but the speaker discusses tapering purchases, the timing of liftoff, or beginning to "
                 "normalize."),
    4: (300 / 6, "NEAR NEUTRAL. Supports policy around neutral: gradual or paused hikes from low levels "
                 "toward neutral, holding near neutral, modest 'insurance' cuts, or gradual cuts from a "
                 "restrictive level back toward neutral."),
    5: (400 / 6, "MODERATELY RESTRICTIVE. Supports continued firming above neutral, or holding policy "
                 "restrictive until inflation is clearly on track; resists or delays cuts."),
    6: (500 / 6, "STRONG TIGHTENING. Supports substantial further hikes and/or faster balance-sheet "
                 "runoff, with clear urgency about inflation."),
    7: (100.0, "AGGRESSIVE TIGHTENING. Supports outsized or very rapid hikes; inflation fight overrides "
               "other concerns, including accepting a recession."),
}


@dataclass(frozen=True)
class Bank:
    analyst: str        # "a senior Federal Reserve analyst"
    official: str       # "a Federal Reserve official"
    policy_area: str    # "U.S. monetary policy"
    appendix: str = ""  # facts about the bank; ends with a blank line when not empty


def score_model(policy_area: str) -> type[BaseModel]:
    class SpeechScore(BaseModel):
        policy_relevant: bool = Field(description=f"True if the speech reveals the speaker's view on the stance of {policy_area}.")
        level: int = Field(ge=0, le=7, description="1-7 on the absolute scale; 0 if policy_relevant is false.")
        evidence: List[str] = Field(description="2-4 short verbatim quotes from the speech supporting the level; empty if not relevant.")
        rationale: str = Field(description="One or two sentences in English: what the speaker says about the policy outlook "
                                           "and why that is this level. If not relevant, what the speech is about.")
    return SpeechScore


CORE = """You are {analyst}. You classify a single speech by {official}
on an ABSOLUTE easy-to-tight scale of the monetary policy the speaker supports.

Step 1 - relevance. Set policy_relevant=false if the speech does not reveal the speaker's view on the stance of
{policy_area} (interest rates, asset purchases/balance sheet, or the policy outlook). Speeches only about
bank supervision, regulation, payments, fintech, community development, education, history or tributes are NOT
relevant, unless they contain a clear statement about the monetary policy stance. Then level=0, evidence=[].

Step 2 - level. Judge the policy the SPEAKER supports: where it is and where they want it to go.
The scale is absolute, not relative to the current setting: "keep rates at zero with ongoing asset purchases" is
level 2 whenever it is said, and "hold rates at a restrictive level" is level 5 whenever it is said.
Use the policy rate provided to place the speaker correctly. Base the judgement on the speaker's own
policy views; ignore sections that merely describe history or other people's views.
Score what the speaker says policy should do, not how high inflation or growth is: an official who calls inflation
too high but says the current stance is well positioned to bring it down is endorsing a hold, not a tightening.

Holding rates is the most common stance. When the speaker supports holding the current setting:
- first find the level that describes a balanced hold at the current setting (e.g. near zero with no liftoff in
  view = 2, near neutral = 4, clearly restrictive = 5);
- if the speaker explicitly signals readiness to tighten next (e.g. "prepared to raise rates", "we have more work
  to do", resists any easing), move ONE level up;
- if the speaker explicitly signals readiness to ease next (e.g. cuts likely soon, downside risks dominate),
  move ONE level down;
- a hold described as well positioned, with no lean either way, stays at the balanced-hold level.

Balance sheet. Read the speaker's balance-sheet stance from the speech itself:
- supporting starting, expanding or continuing large-scale asset purchases is easing;
- supporting tapering or ending purchases, or starting or speeding up balance-sheet runoff, is tightening;
- ending runoff, or purchases of Treasury bills described as reserve management or as keeping
  reserves ample, are technical operations that do not by themselves change the stance; judge the stance from
  the speaker's views on the policy rate in that case.

{appendix}Levels:
{levels}

Evidence: copy 2-4 short quotes EXACTLY as they appear in the speech (no paraphrasing, no ellipses).
Respond with JSON only."""


def system_prompt(bank: Bank) -> str:
    return CORE.format(analyst=bank.analyst, official=bank.official, policy_area=bank.policy_area,
                       appendix=bank.appendix, levels="\n".join(f"{k}. {v[1]}" for k, v in LEVELS.items()))


def user_prompt(speech: dict, setting: str) -> str:
    """setting: the bank's policy setting on the speech date, as a sentence ("federal funds target rate 5%.")."""
    return (
        f"Speaker: {speech['speaker']} ({speech['role']})\n"
        f"Date: {speech['date']}\n"
        f"Title: {speech['title']}\n"
        f"Policy setting on this date: {setting}\n\n"
        f"SPEECH TEXT:\n{speech['text'][:MAX_TEXT_CHARS]}"
    )


def level_to_value(level: int) -> float:
    return LEVELS[level][0]
