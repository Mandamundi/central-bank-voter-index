"""CANDIDATE shared scoring rubric v10: the directional 11-level policy-pressure scale.

Pilot copy. Nothing in the three projects imports this file. Once the pilot is accepted it replaces
rubric.py in all three projects (same checksum procedure as before), which starts the v10 score vintage.

The scale asks one question: how far, and how urgently, does the speaker want policy to move from the
current setting over the near term? 50 is a hold with no lean; below 50 is pressure to ease, above 50
pressure to tighten. The level of rates itself is not scored: the same view gets the same level in any
era and at any bank.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel, Field

VERSION = "v10"
MAX_TEXT_CHARS = 150_000

# level -> (0-100 value, definition). The scale is DIRECTIONAL: it measures the change the speaker wants
# from the current setting, not how easy or tight that setting is.
LEVELS = {
    0: (0.0, "EMERGENCY EASING. A crisis response: rapid cuts to or near the floor, launching or expanding "
             "large-scale asset purchases or emergency facilities, 'use all our tools'; or supporting the "
             "emergency response while the crisis is still unfolding."),
    1: (10.0, "AGGRESSIVE EASING. Outside a crisis, cuts larger than the bank's usual step or a rapid series "
              "of cuts, or a large new easing package; or more or faster easing than the committee is doing."),
    2: (20.0, "EASING NOW. Supports a cut or added easing (more purchases, a deeper negative rate, guidance "
              "that commits to easier policy) at the coming meetings at the usual pace, or continuing an "
              "easing cycle."),
    3: (30.0, "LEANS TO EASE. Holds for now but expects or favors easing within the horizon, or favors it if "
              "a named development the speaker sees as likely or as a material risk occurs."),
    4: (40.0, "TILTS TO EASE. Holds with no move expected within the horizon, but sees the risks to the "
              "policy outlook skewed toward easing, is more inclined to ease than to tighten, or sees easing "
              "as the eventual direction beyond the horizon."),
    5: (50.0, "HOLD. Wants the current setting kept, with no lean either way; balanced risks."),
    6: (60.0, "TILTS TO TIGHTEN. Holds with no move expected within the horizon, but sees the risks to the "
              "policy outlook skewed toward tightening, is more inclined to tighten than to ease, or sees "
              "tightening as the eventual direction beyond the horizon."),
    7: (70.0, "LEANS TO TIGHTEN. Holds for now but expects or favors tightening within the horizon (including "
              "preparing to taper purchases or to lift off), or favors it if a named development the speaker "
              "sees as likely or as a material risk occurs."),
    8: (80.0, "TIGHTENING NOW. Supports a hike, tapering or ending purchases, or starting or speeding up "
              "balance-sheet runoff at the coming meetings at the usual pace, or continuing a tightening cycle."),
    9: (90.0, "FASTER TIGHTENING. Hikes larger than the bank's usual step or a rapid series of hikes; or more "
              "or faster tightening than the committee is doing."),
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
        policy_relevant: bool = Field(description=f"True if the speech reveals the speaker's own view on where {policy_area} should go.")
        evidence: List[str] = Field(description="2-4 short verbatim quotes from the speech that show the speaker's view on policy; empty if not relevant.")
        rationale: str = Field(description="One or two sentences in English, written as a summary for readers: what the "
                                           "speaker says about where policy should go and why. Do not mention levels, numbers "
                                           "on the scale or these instructions. If not relevant, what the speech is about.")
        level: Optional[int] = Field(ge=0, le=10, description="0-10 on the directional scale, chosen from the quotes and "
                                                                  "summary above; null if policy_relevant is false.")
    return SpeechScore


CORE = """You are {analyst}. You classify a single speech by {official}
on a DIRECTIONAL scale: how far, and how urgently, the speaker wants monetary policy to move from its current
setting over the near term. The middle of the scale is a hold with no lean; lower is pressure to ease, higher is
pressure to tighten.

Step 1 - relevance. Set policy_relevant=false if the speech does not reveal the speaker's own view on where
{policy_area} should go (interest rates, asset purchases/balance sheet, or the policy outlook). Speeches only about
bank supervision, regulation, payments, fintech, community development, education, history or tributes are NOT
relevant, unless they contain a clear statement about the monetary policy stance. A text that only restates the
bank's latest decision or its standard wording, without the speaker's own view of where policy should go, is also
NOT relevant. Then level=null, evidence=[].

Step 2 - level. Place the speaker by the change in policy they want over the near term: the next few policy
meetings, roughly six months.
- The current setting is the starting point, whatever it is. Do not judge whether it is easy or tight: the scale
  measures only the move the speaker wants from it. "Keep policy where it is", with no lean, is level 5 whether rates
  are at zero or high, and "cut at the next meeting" is level 2 whenever it is said.
- A move the committee has already made is part of the current setting, not a move the speaker wants. A speaker who
  endorses a decision just taken and wants nothing further holds; one who says more of the same will be needed
  wants further moves.
- Score what the speaker says policy should do, not how high inflation or growth is: an official who calls inflation
  too high but says the current stance is well positioned to bring it down supports a hold.
- Base the judgement on the speaker's own views; ignore sections that merely describe history or other people's
  views. A speaker who explains and endorses the committee's current policy, including in the institution's voice
  ("we", "the Committee"), is stating their own view; an explicit statement of personal disagreement overrides it.
- When the speaker favors a different move from the committee (a dissent, or a proposal), score the move the
  speaker favors.

Holds and leans:
- A lean is a stated direction for the next move: "further increases may be needed", "cuts are likely later this
  year", "upside risks dominate". Standard wording such as "data dependent", "we will act as appropriate" or "we
  will not hesitate to take additional measures if necessary" is not a lean.
- A conditional statement is a lean, not support for a move, only when it names a specific development the speaker
  sees as likely or as a material risk ("if inflation persists, we would need to raise rates"). A generic readiness
  to act if necessary is standard wording, not a lean.
- Lean (3 or 7) or tilt (4 or 6): a lean expects or favors a move within the horizon; a tilt expects no move within
  it but points one way (risks skewed, more inclined one way, or the eventual direction lies beyond the horizon).
- Intensity. Moves at the bank's usual step and pace are 2 or 8. Larger steps, a faster sequence, or more than the
  committee is doing are 1 or 9. A speaker who favors a smaller or slower move than the committee is still
  scored by the move they favor.
- Crisis. While the speaker presents a crisis as still unfolding and supports the emergency response (including
  further emergency measures as it develops), the level is 0. Once the speaker treats the emergency measures as in
  place and sufficient, score the change they want from there: keeping those measures in place is then a hold.

Balance sheet and other tools. Read the speaker's stance from the speech itself:
- supporting starting or expanding large-scale asset purchases is easing; continuing purchases already under way,
  at their current pace, is part of the current setting;
- supporting tapering or ending purchases, or starting or speeding up balance-sheet runoff, is tightening;
- ending runoff, or purchases of short-term government bills described as reserve management or as keeping
  reserves ample, are technical operations that do not by themselves change the stance; judge the stance from
  the speaker's views on the policy rate in that case;
- lending or funding schemes for banks are easing only when the speaker presents them as monetary stimulus;
  asset purchases made as a temporary financial stability operation are not easing;
- a change to forward guidance or to a yield target is tightening when the speaker presents it as a step toward
  removing accommodation, easing when presented as adding stimulus, and no change in stance when presented as a
  way to sustain the current policy;
- when tools point different ways, score the overall direction the speaker presents; if unclear, the policy rate
  decides.

{appendix}Levels (each wants tighter policy than the one before):
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
        f"Current policy setting on this date: {setting}\n\n"
        f"SPEECH TEXT:\n{speech['text'][:MAX_TEXT_CHARS]}"
    )


def level_to_value(level: int) -> float:
    return LEVELS[level][0]
