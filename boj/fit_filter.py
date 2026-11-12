"""Fit the stance filter's five parameters to this bank's speech history (data/filter_params.json).

Run it deliberately, like a rubric change, not in the daily job: new parameters revise the whole index
history. Refit after a rescore, or once a year as speeches accumulate; check that the test still favours
the filter, then rebuild the index.

    uv run fit_filter.py            # fit, test, and write data/filter_params.json
    uv run fit_filter.py --check    # fit and test only; write nothing

The test fits on the earliest 60% of readings and predicts each later one before seeing it, against
carry-forward (the speaker's previous reading). The stored parameters are then fitted on all readings.
The file is the same in all three banks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import build_index
import config
import stance_filter
import voters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fit and test only; write nothing")
    args = parser.parse_args()
    _, readings = build_index.filter_observations(build_index.policy_speeches(), voters.Roster())
    test_from = sorted(d for d, _, _ in readings)[int(0.6 * len(readings))]
    train, _ = stance_filter.fit(readings, until=test_from)
    test = stance_filter.prediction_test(readings, train, start=test_from)
    params, loglik = stance_filter.fit(readings)
    print(f"{len(readings)} readings, {readings[0][0]} to {readings[-1][0]}")
    print("test from", test_from, json.dumps(test))
    print("parameters:", {k: round(v, 2) for k, v in params.items()})
    if test.get("n") and test["rmse_filter"] > test["rmse_carry_forward"]:
        print("warning: the filter predicts worse than carry-forward on held-out readings")
    if not args.check:
        store_json = {"fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      "scorer_version": config.SCORER_VERSION, "readings": len(readings),
                      "first_reading": readings[0][0], "last_reading": readings[-1][0],
                      "params": params, "log_likelihood": loglik,
                      "test": {"from": test_from, "params_fitted_before": train, **test}}
        path = build_index.FILTER_PARAMS
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store_json, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
