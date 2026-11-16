"""The hand-check sheet: which claims get picked, that the pick is repeatable, and that web text stays text.
Nothing here touches the network.

    cd services/orchestrator
    python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import __main__ as cli  # noqa: E402
from orchestrator import handcheck  # noqa: E402
from orchestrator.appclient import AppClient  # noqa: E402

RUN_ID = "11111111-2222-4333-8444-555555555555"


def claim(ref, verdict, text=None, url=None, evidence=None):
    return {"ref": ref, "text": text or f"Claim {ref} about a launch.", "source_url": url or f"https://news.example.com/{ref}",
            "verdict": verdict, "evidence": evidence if evidence is not None else
            (f"Excerpt for {ref} on the page itself." if verdict == "supported" else None)}


def item(rank, title, claims, *, claims_run=3):
    return {"rank": rank, "development": {"title": title}, "claims": claims, "claims_run": claims_run}


def make_run(*, supported=14, unsupported=3, unreachable=2):
    """Run number 3 with one complete campaign (its claims were checked in run 3), one that carries an item over
    from run 2, and one failed campaign that shows an older list."""
    refs = iter(range(1, 1000))
    claims = ([claim(f"s{next(refs)}", "supported") for _ in range(supported)]
              + [claim(f"u{next(refs)}", "unsupported") for _ in range(unsupported)]
              + [claim(f"r{next(refs)}", "unreachable") for _ in range(unreachable)])
    per = 4
    top = [item(i + 1, f"Development {i + 1}", claims[i * per:(i + 1) * per]) for i in range((len(claims) + per - 1) // per)]
    top.append(item(len(top) + 1, "Carried over", [claim("old1", "supported"), claim("old2", "unsupported")], claims_run=2))
    return {"id": RUN_ID, "number": 3, "campaigns": [
        {"title": "Campaign A", "status": "complete", "top": top},
        # A failed campaign has no list of its own. Even if the app handed one over, it is not this run's work.
        {"title": "Campaign B", "status": "failed", "top": [item(1, "Shown by mistake", [claim("failed1", "unsupported")])],
         "stale_top": [item(1, "Stale", [claim("stale1", "unsupported")], claims_run=1)]},
    ]}


class CollectTests(unittest.TestCase):
    def test_only_claims_checked_in_this_run_are_used(self):
        found = handcheck.collect(make_run())
        self.assertEqual((len(found["supported"]), len(found["unsupported"]), len(found["unreachable"])), (14, 3, 2))
        refs = {c["ref"] for v in found.values() for c in v}
        self.assertNotIn("old1", refs, "a development carried over from an earlier run was checked in that run")
        self.assertNotIn("old2", refs)
        self.assertNotIn("stale1", refs)
        self.assertNotIn("failed1", refs, "a failed campaign did no checking in this run")

    def test_a_claim_with_an_unknown_label_is_ignored_not_trusted(self):
        run = make_run(supported=1, unsupported=0, unreachable=0)
        run["campaigns"][0]["top"][0]["claims"].append(claim("odd", "definitely-true"))
        found = handcheck.collect(run)
        self.assertEqual([c["ref"] for c in found["supported"]], ["s1"])
        self.assertNotIn("odd", {c["ref"] for v in found.values() for c in v})

    def test_a_run_with_nothing_in_it_gives_empty_lists(self):
        found = handcheck.collect({"number": 1, "campaigns": []})
        self.assertEqual({k: len(v) for k, v in found.items()}, {"supported": 0, "unsupported": 0, "unreachable": 0, "unverified": 0})


class PickTests(unittest.TestCase):
    def setUp(self):
        self.found = handcheck.collect(make_run(supported=25))

    def test_exactly_ten_of_the_supported_claims_are_picked_and_they_are_all_supported(self):
        sample = handcheck.pick(self.found, 10, "seed")
        self.assertEqual(len(sample), 10)
        self.assertEqual(len({c["ref"] for c in sample}), 10)
        self.assertTrue(all(c["ref"].startswith("s") for c in sample))

    def test_the_same_seed_gives_the_same_pick_and_other_seeds_can_differ(self):
        again = handcheck.pick(self.found, 10, "seed")
        self.assertEqual([c["ref"] for c in handcheck.pick(self.found, 10, "seed")], [c["ref"] for c in again])
        picks = {tuple(c["ref"] for c in handcheck.pick(self.found, 10, f"seed-{n}")) for n in range(8)}
        self.assertGreater(len(picks), 1, "different seeds should not all give the same ten")

    def test_the_pick_keeps_the_order_the_app_shows(self):
        order = [c["ref"] for c in self.found["supported"]]
        refs = [c["ref"] for c in handcheck.pick(self.found, 10, "seed")]
        self.assertEqual(refs, sorted(refs, key=order.index))

    def test_fewer_supported_claims_than_asked_gives_all_of_them(self):
        found = handcheck.collect(make_run(supported=4))
        self.assertEqual(len(handcheck.pick(found, 10, "seed")), 4)

    def test_the_number_asked_for_is_kept_sensible(self):
        self.assertEqual(handcheck.pick(self.found, 0, "seed"), [])
        self.assertEqual(handcheck.pick(self.found, -5, "seed"), [])
        self.assertEqual(len(handcheck.pick(self.found, 10_000, "seed")), 25)

    def test_the_default_seed_belongs_to_the_run(self):
        self.assertEqual(handcheck.default_seed(make_run()), RUN_ID)
        self.assertEqual(handcheck.default_seed({"number": 7}), "7")


class SheetTests(unittest.TestCase):
    def sheet(self, run=None, samples=10, seed="s"):
        run = run or make_run()
        found = handcheck.collect(run)
        sample = handcheck.pick(found, samples, seed)
        return handcheck.render(run, found, sample, samples=samples, seed=seed), found, sample

    def test_every_unsupported_claim_is_on_the_sheet_and_exactly_the_picked_supported_ones(self):
        text, found, sample = self.sheet()
        for c in found["unsupported"]:
            self.assertIn(c["text"], text.replace("\\", ""))
        self.assertEqual(text.count("**A"), 3)
        self.assertEqual(text.count("**B"), 10)
        for c in found["supported"]:
            self.assertEqual(c in sample, c["text"] in text.replace("\\", ""), c["ref"])

    def test_a_supported_claim_shows_its_evidence_and_an_unsupported_one_shows_none(self):
        text, found, sample = self.sheet(samples=100)
        self.assertEqual(text.count("- Excerpt: “"), len(sample))
        self.assertIn("Excerpt for s1 on the page itself.", text.replace("\\", ""))
        unsupported_part = text.split("## B.")[0]
        self.assertNotIn("Excerpt:", unsupported_part)

    def test_the_counts_in_the_header_match_the_run(self):
        text, _, _ = self.sheet()
        self.assertIn("Claims checked in this run: 19: 14 supported, 3 unsupported, 2 unreachable, 0 unverified", text)
        self.assertIn("Random pick: 10 of 14 supported claims", text)

    def test_a_run_without_unsupported_claims_says_so(self):
        text, _, _ = self.sheet(make_run(unsupported=0))
        self.assertIn("## A. Every unsupported claim (0)", text)
        self.assertIn("None this run.", text)

    def test_web_text_stays_text(self):
        def hostile(ref, verdict):
            return {"ref": ref, "verdict": verdict, "source_url": "https://news.example.com/" + ref,
                    "text": "Click [here](http://evil.example/login) <script>alert(1)</script>\x1b[31m red",
                    "evidence": ("![pixel](http://evil.example/p.png) <img src=x onerror=alert(1)> `code` \x07 bell"
                                 if verdict == "supported" else None)}

        claims = [hostile("a", "supported"), hostile("b", "unsupported"), hostile("c", "unreachable")]
        run = {"id": RUN_ID, "number": 1, "campaigns": [{"title": "C <b>bold</b>", "status": "complete", "top": [
            {"rank": 1, "development": {"title": "T [link](http://evil.example)"}, "claims": claims, "claims_run": 1}]}]}
        text, _, _ = self.sheet(run)
        for bad in ("<script>", "<img", "\x1b", "\x07", "](http://evil.example", "![pixel]", "<b>"):
            self.assertNotIn(bad, text, bad)
        self.assertEqual(text.count("&lt;script&gt;"), 3, "the claim text appears once in each of the three sections")

    def test_a_page_address_that_is_not_http_is_not_made_a_link(self):
        run = make_run(supported=0, unsupported=1, unreachable=0)
        run["campaigns"][0]["top"][0]["claims"][0]["source_url"] = "javascript:alert(1)"
        text, _, _ = self.sheet(run)
        self.assertNotIn("](javascript:", text)


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append((method, url))
        path = url.split("://", 1)[1].split("/", 1)[1]
        key = (method, "/" + path)
        if key not in self.routes:
            return FakeResponse(404, {"error": "Not found."})
        return FakeResponse(200, self.routes[key])


class NoTrace:
    def event(self, *args, **kwargs):
        pass


def app_with(routes):
    app = AppClient(NoTrace(), base_url="http://app.test", username="u", password="p", session=FakeSession(routes),
                    sleep=lambda s: None)
    app.token = "t"
    return app


class AppClientTests(unittest.TestCase):
    def test_the_latest_run_is_one_request(self):
        run = make_run()
        app = app_with({("GET", "/api/tracker/runs/latest"): {"run": run}})
        self.assertEqual(app.run_detail(), run)
        self.assertEqual(app.session.calls, [("GET", "http://app.test/api/tracker/runs/latest")])

    def test_a_run_by_number_is_found_in_the_list_then_fetched(self):
        run = make_run()
        app = app_with({("GET", "/api/tracker/runs"): {"runs": [{"id": "id-2", "number": 2}, {"id": "id-3", "number": 3}]},
                        ("GET", "/api/tracker/runs/id-3"): {"run": run}})
        self.assertEqual(app.run_detail(3), run)

    def test_a_run_number_that_does_not_exist_gives_none(self):
        app = app_with({("GET", "/api/tracker/runs"): {"runs": [{"id": "id-1", "number": 1}]}})
        self.assertIsNone(app.run_detail(9))

    def test_an_id_from_the_app_cannot_change_the_path(self):
        app = app_with({("GET", "/api/tracker/runs"): {"runs": [{"id": "../../auth/login", "number": 1}]},
                        ("GET", "/api/tracker/runs/..%2F..%2Fauth%2Flogin"): {"run": None}})
        app.run_detail(1)
        self.assertEqual(app.session.calls[-1][1], "http://app.test/api/tracker/runs/..%2F..%2Fauth%2Flogin")

    def test_no_runs_yet(self):
        app = app_with({("GET", "/api/tracker/runs/latest"): {"run": None}})
        self.assertIsNone(app.run_detail())


class CommandTests(unittest.TestCase):
    """The command itself, with the app faked: it prints the sheet and saves it next to the other reports."""

    def run_command(self, run, argv):
        said, folder = [], tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)

        class App:
            def run_detail(self, number=None):
                return run

        with mock.patch.object(cli, "_connect", return_value=(App(), {}, NoTrace_close())), \
                mock.patch.object(cli, "REPORTS", Path(folder.name)), mock.patch.object(cli, "say", said.append):
            code = cli.main(argv)
        return code, "\n".join(said), Path(folder.name)

    def test_it_prints_the_sheet_and_saves_it(self):
        code, output, folder = self.run_command(make_run(), ["handcheck"])
        self.assertEqual(code, 0)
        self.assertIn("# Hand-check sheet: run 3", output)
        saved = (folder / "handcheck-run3.md").read_text(encoding="utf-8")
        self.assertIn("## A. Every unsupported claim (3)", saved)
        self.assertEqual(saved.count("**B"), 10)

    def test_the_same_run_gives_the_same_pick_twice_and_a_seed_changes_it(self):
        def sheet(*argv):   # the last line names a temporary folder, which differs every time
            return self.run_command(make_run(supported=30), ["handcheck", *argv])[1].rsplit("\nSaved as", 1)[0]

        self.assertEqual(sheet(), sheet())
        self.assertGreater(len({sheet("--seed", str(n)) for n in range(6)}), 1)

    def test_samples_can_be_changed(self):
        _, output, _ = self.run_command(make_run(supported=30), ["handcheck", "--samples", "4"])
        self.assertEqual(output.count("**B"), 4)

    def test_no_saved_run_is_said_plainly(self):
        code, output, _ = self.run_command(None, ["handcheck"])
        self.assertEqual(code, 1)
        self.assertIn("No saved run to check", output)


class NoTrace_close:
    def close(self):
        pass


if __name__ == "__main__":
    unittest.main()
