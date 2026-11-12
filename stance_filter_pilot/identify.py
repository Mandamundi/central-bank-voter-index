"""Can a bank's own data pin the model down? Run after stance_filter.py, from the bank's folder:

    cd boe && .venv/bin/python ../stance_filter_pilot/identify.py boe

1. Likelihood-ratio test: does dropping the two rate-change jump parameters significantly worsen the fit?
   (2 x log-likelihood gain above 5.99 = they earn their place at the 5% level.)
2. Profile of each half-life: refit everything else with that half-life held at 30 days to 10 years, and
   report how much the fit and the published index move. A number the data cannot pin down only matters
   if the index changes with it.
"""
import csv
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stance_filter as sf  # noqa: E402

obs, moves, on, names, people, fit_end, live, live_members = sf.load()
P = sf.PARAMS
start = min(live)


def fit_fixed(fixed):
    """Maximise the likelihood over the free parameters; `fixed` maps name -> value (0 allowed for jumps)."""
    free = [k for k in P if k not in fixed]
    def theta(zf):
        vals = dict(zip(free, (math.exp(v) for v in zf)))
        vals.update(fixed)
        return [vals[k] for k in P]
    init = {"sd_c": 12, "halflife_c": 180, "sd_d": 10, "halflife_d": 120, "jump_c": 5, "jump_d": 5, "noise": 8}
    nll = lambda zf: -sf.run(theta(zf), obs, moves, people)
    z, v = sf.nelder_mead(nll, [math.log(init[k]) for k in free])
    z, v = sf.nelder_mead(nll, z, step=0.15)
    return theta(z), -v


def index_series(theta):
    series, _ = sf.daily_series(theta, obs, moves, people, on, start)
    return np.array([m for _, m, _ in series])


full, ll_full = fit_fixed({})
nojump, ll_nojump = fit_fixed({"jump_c": 0.0, "jump_d": 0.0})
lr = 2 * (ll_full - ll_nojump)
print(f"{sf.BANK}: log-lik with jumps {ll_full:.1f}, without {ll_nojump:.1f}; LR = {lr:.2f} "
      f"({'jumps earn their place' if lr > 5.99 else 'jumps not supported by the data'})")
base = nojump if lr <= 5.99 else full
base_fixed = {"jump_c": 0.0, "jump_d": 0.0} if lr <= 5.99 else {}
ll_base = ll_nojump if lr <= 5.99 else ll_full
base_index = index_series(base)
print("  chosen model:", {k: round(v, 1) for k, v in zip(P, base)})

for hl in ["halflife_c", "halflife_d"]:
    print(f"  profile of {hl} (others refitted):")
    for value in [30, 90, 180, 365, 730, 1825, 3650]:
        theta, ll = fit_fixed({**base_fixed, hl: float(value)})
        diff = np.abs(index_series(theta) - base_index)
        print(f"    {value:>5} days: log-lik {ll - ll_base:+7.2f}   index vs chosen: mean |diff| {diff.mean():4.2f}, max {diff.max():5.2f}")

out = Path(sf.OUT) / "chosen_params.csv"
with open(out, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["param", "value"]); w.writerows(zip(P, base)); w.writerow(["jumps_kept", lr > 5.99])
print("  wrote", out)
