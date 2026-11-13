"""FOMC dissents from the minutes since 1996, with direction, for the Fed vote test. Collected and checked
before any Fed comparison is run.

    cd fed_speech_index && .venv/bin/python ../comparative_pilot/fed_dissents.py

For each meeting in data/voters.csv: the 'Voting against this action' line of the policy vote and the reason
that follows. Direction: +1 tighter (preferred a higher rate / firmer policy), -1 looser, None unplaced
(printed for checking by hand). Writes comparative_pilot/fed/dissents.json.
"""
import csv, json, os, re, sys, time
from pathlib import Path
sys.path.insert(0, os.getcwd())
import http_get, voters

OUT = Path(__file__).resolve().parent / "fed" / "dissents.json"
TIGHTER = (r"preferred (?:an? )?(?:increase|to (?:raise|increase|tighten|firm)|tighten|firm|raising|a higher|a \d+[- ]basis[- ]point increase)"
           r"|in favor of (?:an? )?(?:increase|raising|tightening|firming)|tilt(?:ed)? toward (?:tightening|firming)|asymmetric(?:al)? toward (?:tightening|firming)"
           r"|reduce(?:d)? (?:the )?(?:pace|amount) of (?:asset|securities) purchases|end (?:the )?(?:asset|securities) purchases|raise (?:the )?target")
LOOSER = (r"preferred (?:an? )?(?:decrease|reduction|cut|to (?:lower|reduce|cut|ease|leave|maintain|keep)|easing|lowering|a lower|a \d+[- ]basis[- ]point (?:reduction|decrease|cut)|no change)"
          r"|in favor of (?:an? )?(?:reduction|cut|easing|lowering)|tilt(?:ed)? toward (?:easing|ease)|asymmetric(?:al)? toward (?:easing|ease)|additional (?:monetary )?(?:policy )?accommodation|lower the target")


def against_lines(page):
    """Every 'Vote(s)/Voting against this action' line that names someone, with the 900 characters after it.
    Minutes can hold several votes (money-growth ranges, the policy directive, statement wording); all are
    kept and each dissent is coded by hand in dissent_codes.json."""
    text = re.sub(rf"\b{voters._TITLES}\s*", "", voters._text(page))
    out = []
    for m in voters._AGAINST_LINE.finditer(text):
        names = [n for n in voters.parse_vote_names(m.group(1)) if n != "None"]
        # parse_vote_names drops a name that follows a dissent reason and is followed by "and"
        # ("...; and Austan D. Goolsbee and Jeffrey R. Schmid, who ..."): add surnames found in the line.
        raw = re.sub(r"\bwho\b[^;]*", "", m.group(1))
        for n in re.findall(r"([A-Z][a-z]+(?: [A-Z]\.)? [A-Z][a-z]+)(?= and | who|,|;|$)", raw):
            if not any(n.split()[-1] == x.split()[-1] for x in names):
                names.append(n)
        if names:
            out.append((names, text[m.start():m.start() + 900]))
    return out


EXTRA = [("2008-06-25", "https://www.federalreserve.gov/monetarypolicy/fomcminutes20080625.htm")]   # missing from voters.csv


def main():
    rows = [(r["date"], r["source"]) for r in csv.DictReader(open("data/voters.csv"))] + EXTRA
    out = []
    for date, url in sorted(rows):
        page = None
        for attempt in range(3):
            try:
                page = http_get.get(url).text; break
            except Exception:
                time.sleep(3)
        if page is None:
            out.append({"date": date, "error": "fetch failed"}); continue
        for k, (names, seg) in enumerate(against_lines(page)):
            for n in names:
                out.append({"date": date, "vote": k, "name": n, "line": seg})
        time.sleep(0.3)
    OUT.write_text(json.dumps(out, indent=1))
    print(len(out), "dissents;", sum("error" in d for d in out), "fetch failures")


if __name__ == "__main__":
    main()
