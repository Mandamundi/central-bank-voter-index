"""The stance filter (stance_filter.py). The same file is in all three banks."""
import json
import unittest

import config
import stance_filter

PARAMS = {"sd_c": 12.0, "halflife_c": 250.0, "sd_d": 8.5, "halflife_d": 380.0, "noise": 8.5}


def days(first, last):
    import datetime as dt
    d, out = dt.date.fromisoformat(first), []
    while d <= dt.date.fromisoformat(last):
        out.append(d.isoformat()); d += dt.timedelta(days=1)
    return out


class StanceFilterTests(unittest.TestCase):
    def test_stored_parameters_are_the_five_fitted_numbers(self):
        stored = json.loads((config.DATA / "filter_params.json").read_text())
        self.assertEqual(set(stored["params"]), set(stance_filter.PARAMS))
        self.assertTrue(all(v > 0 for v in stored["params"].values()))

    def test_a_fresh_reading_moves_the_estimate_most_of_the_way_but_not_all(self):
        obs = stance_filter.prepare([("2020-01-01", "a", 90, "2020-01-01")])
        row = stance_filter.daily(obs, ["2020-01-01"], lambda d: ["a", "b"], PARAMS)[0]
        estimate, _ = row["members"]["a"]
        self.assertGreater(estimate, 70)
        self.assertLess(estimate, 90)

    def test_a_silent_voter_drifts_toward_the_committee_as_others_speak(self):
        readings = [("2020-01-01", "a", 90, "2020-01-01")]
        readings += [(d, "b", 30, d) for d in days("2020-02-01", "2021-01-01")[::30]]
        rows = stance_filter.daily(stance_filter.prepare(readings), ["2020-01-02", "2021-01-02"], lambda d: ["a", "b"], PARAMS)
        (early, early_sd), (late, late_sd) = rows[0]["members"]["a"], rows[1]["members"]["a"]
        self.assertLess(late, early)
        self.assertGreater(late_sd, early_sd * 0.9)

    def test_the_index_is_the_equal_weight_mean_of_the_estimates(self):
        obs = stance_filter.prepare([("2020-01-01", "a", 90, "2020-01-01"), ("2020-01-01", "b", 20, "2020-01-01")])
        row = stance_filter.daily(obs, ["2020-01-05"], lambda d: ["a", "b", "c"], PARAMS)[0]
        self.assertAlmostEqual(row["index"], sum(m for m, _ in row["members"].values()) / 3)

    def test_no_look_ahead(self):
        base = [("2020-01-01", "a", 60, "2020-01-01")]
        later = base + [("2020-03-01", "a", 100, "2020-03-01")]
        span = days("2020-01-01", "2020-02-28")
        one = stance_filter.daily(stance_filter.prepare(base), span, lambda d: ["a"], PARAMS)
        two = stance_filter.daily(stance_filter.prepare(later), span, lambda d: ["a"], PARAMS)
        self.assertEqual([r["index"] for r in one], [r["index"] for r in two])

    def test_late_publication_of_an_older_text_cannot_replace_a_newer_one(self):
        kept = stance_filter.prepare([("2020-01-03", "a", 80, "2020-01-03"), ("2020-01-05", "a", 10, "2020-01-01")])
        self.assertEqual(kept, [("2020-01-03", "a", 80.0)])

    def test_same_day_texts_are_one_averaged_reading(self):
        kept = stance_filter.prepare([("2020-01-01", "a", 80, "2020-01-01"), ("2020-01-02", "a", 40, "2020-01-01")])
        self.assertEqual(kept, [("2020-01-02", "a", 60.0)])

    def test_fit_recovers_parameters_that_predict_better_than_noise(self):
        wobble = [3, -5, 1, 6, -2, -4, 5, 0, -6, 2]    # fixed, so the test is repeatable
        readings = [(d, p, 50 + (20 if p == "a" else -20) + wobble[(k + (p == "b")) % 10], d)
                    for k, d in enumerate(days("2020-01-01", "2021-12-31")[::20]) for p in "ab"]
        obs = stance_filter.prepare(readings)
        params, _ = stance_filter.fit(obs)
        test = stance_filter.prediction_test(obs, params, start="2021-01-01")
        self.assertLess(test["mae_filter"], 10)


if __name__ == "__main__":
    unittest.main()
