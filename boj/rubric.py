"""Shared scoring rubric: the absolute 11-level policy-stance scale, the prompt core and the response schema.

This file is identical in the Fed, BOJ and BOE projects, so every bank's speeches are scored with the
same instrument. Each project's prompt.py fills in the bank's names and adds a short appendix of facts
about the bank (who votes, its policy instruments); the appendix never says which level to give.
tests/test_rubric.py pins this file's checksum: after an edit, copy it to all three projects, update
the checksum in all three tests and bump VERSION (which starts a new score vintage everywhere).

The scale asks one question: how tight is the policy the speaker wants over the near term (the
current setting plus the moves they support or lean toward)? Each level is tighter than the one
before, so a speaker who wants a move never scores below one who only leans toward it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel, Field

VERSION = "v7"
MAX_TEXT_CHARS = 150_000

# level -> (0-100 value, definition). The scale is ABSOLUTE: it measures how easy or tight
# the policy the speaker wants is, on one fixed yardstick across all eras and banks.
LEVELS = {
    0: (0.0, "EMERGENCY EASING. A crisis response: cutting rapidly to or near the floor, launching or "
             "expanding large-scale asset purchases, or emergency facilities; 'use all our tools'."),
    1: (10.0, "MORE EASING FROM THE FLOOR. Rates are at or near the floor (zero or negative) and the speaker "
              "wants, or leans toward, still more easing: more purchases, deeper negative rates, stronger "
              "guidance."),
    2: (20.0, "MAXIMUM ACCOMMODATION HELD. Rates at the floor with ongoing asset purchases, or a promise "
              "that rates stay very low for a long time; tightening is not in view. Also: supporting or "
              "leaning toward more easing from clearly accommodative policy, or large or repeated cuts to "
              "well below neutral in a downturn."),
    3: (30.0, "ACCOMMODATIVE, PREPARING TO WITHDRAW SUPPORT. Holding clearly accommodative policy with no "
              "lean, or, from the floor, discussing tapering, ending purchases or the timing of liftoff."),
    4: (40.0, "MILDLY ACCOMMODATIVE. Raising rates gradually from low levels toward neutral (including "
              "liftoff), or holding accommodative policy while leaning toward tightening. Also: modest "
              "cuts to just below neutral ('insurance' cuts), or holding near neutral while leaning toward "
              "easing."),
    5: (50.0, "NEUTRAL. Holding policy around neutral with no lean, or cutting from restrictive policy "
              "back toward neutral."),
    6: (60.0, "MILDLY RESTRICTIVE. Holding restrictive policy while leaning toward easing (cuts in view), "
              "or holding near neutral while leaning toward tightening."),
    7: (70.0, "RESTRICTIVE HOLD. Holding restrictive policy until inflation is clearly on track, with no "
              "lean; resisting or delaying cuts. Also: gradual hikes from neutral into restrictive "
              "territory."),
    8: (80.0, "RESTRICTIVE, LEANING TO TIGHTEN. Holding restrictive policy while leaning toward further "
              "hikes or faster balance-sheet runoff."),
    9: (90.0, "FURTHER TIGHTENING. Raising rates further from a restrictive level, or rapid hikes from low "
              "levels toward a restrictive rate."),
    10: (100.0, "AGGRESSIVE TIGHTENING. Outsized or very rapid hikes with clear urgency; the inflation fight "
                "overrides other concerns, including accepting a recession."),
}


@dataclass(frozen=True)
class Bank:
    analyst: str        # "a senior Federal Reserve analyst"
    official: str       # "a Federal Reserve official"
    policy_area: str    # "U.S. monetary policy"
    appendix: str = ""  # facts about the bank; ends with a blank line when not empty


def score_model(policy_area: str) -> type[BaseModel]:
    class SpeechScore(BaseModel):
        # Field order is output order: the quotes and the summary come before the level, so the level is
        # chosen after the speaker's view has been stated.
        policy_relevant: bool = Field(description=f"True if the speech reveals the speaker's own view on the stance of {policy_area}.")
        evidence: List[str] = Field(description="2-4 short verbatim quotes from the speech that show the speaker's view on policy; empty if not relevant.")
        rationale: str = Field(description="One or two sentences in English, written as a summary for readers: what the "
                                           "speaker says about where policy should go and why. Do not mention levels, numbers "
                                           "on the scale or these instructions. If not relevant, what the speech is about.")
        level: Optional[int] = Field(ge=0, le=10, description="0-10 on the absolute scale, chosen from the quotes and summary "
                                                                  "above; null if policy_relevant is false.")
    return SpeechScore


CORE = """You are {analyst}. You classify a single speech by {official}
on an ABSOLUTE easy-to-tight scale: how tight the monetary policy is that the speaker wants over the near term.

Step 1 - relevance. Set policy_relevant=false if the speech does not reveal the speaker's own view on the stance of
{policy_area} (interest rates, asset purchases/balance sheet, or the policy outlook). Speeches only about
bank supervision, regulation, payments, fintech, community development, education, history or tributes are NOT
relevant, unless they contain a clear statement about the monetary policy stance. A text that only restates the
bank's latest decision or its standard wording, without the speaker's own view of where policy should go, is also
NOT relevant. Then level=null, evidence=[].

Step 2 - level. Place the speaker by the policy they want over the near term: the next few policy meetings, roughly
six months. That is the current setting plus the moves they support or lean toward.
- The scale is absolute, not relative to the current setting: "keep rates at zero with ongoing asset purchases" is
  level 2 whenever it is said, and "hold rates at a restrictive level", with no lean, is level 7 whenever it is said.
- Use the policy setting provided as the starting point. Judge whether policy is accommodative, neutral or
  restrictive from the speaker's own description or the bank's, and otherwise from the setting; never by comparison
  with another country's or another era's rate levels.
- Score what the speaker says policy should do, not how high inflation or growth is: an official who calls inflation
  too high but says the current stance is well positioned to bring it down supports a hold.
- Base the judgement on the speaker's own views; ignore sections that merely describe history or other people's
  views. A speaker who explains and endorses the committee's current policy, including in the institution's voice
  ("we", "the Committee"), is stating their own view; an explicit statement of personal disagreement overrides it.

Holds and leans:
- A lean is a stated direction for the next move ("further increases may be needed", "cuts are likely later this
  year", "upside risks dominate"). Standard wording such as "data dependent", "we will act as appropriate" or "we
  will not hesitate to take additional measures if necessary" is not a lean.
- A conditional statement ("if inflation persists, we would need to raise rates") is a lean, not support for a move.
- Supporting faster or larger moves than a gradual pace counts one level further than supporting gradual moves.

How the current setting and the speaker's view combine:
  setting at the floor (zero or negative, purchases): wants easing 1, leans to ease 1, holds 2, leans to tighten 3, wants tightening 4
  setting accommodative:                              wants easing 2, leans to ease 2, holds 3, leans to tighten 4, wants tightening 4
  setting near neutral:                               wants easing 4, leans to ease 4, holds 5, leans to tighten 6, wants tightening 7
  setting restrictive:                                wants easing 5, leans to ease 6, holds 7, leans to tighten 8, wants tightening 9
Crisis easing from any setting is 0. Outsized or urgent tightening is 10. Rapid hikes from low levels toward a
restrictive rate are 9.

Balance sheet and other tools. Read the speaker's stance from the speech itself:
- supporting starting, expanding or continuing large-scale asset purchases is easing;
- supporting tapering or ending purchases, or starting or speeding up balance-sheet runoff, is tightening;
- ending runoff, or purchases of short-term government bills described as reserve management or as keeping
  reserves ample, are technical operations that do not by themselves change the stance; judge the stance from
  the speaker's views on the policy rate in that case;
- lending or funding schemes for banks are easing only when the speaker presents them as monetary stimulus;
  asset purchases made as a temporary financial stability operation are not easing;
- a change to forward guidance or to a yield target is tightening when the speaker presents it as a step toward
  removing accommodation, and no change in stance when presented as a way to sustain the current easing.

{appendix}Levels (each is tighter than the one before):
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
