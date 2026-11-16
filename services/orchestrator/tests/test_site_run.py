"""A run started from the website: one campaign, the head person's own login, a share of the budget.
Nothing here touches the network."""
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import orchestrator.__main__ as cli  # noqa: E402
from helpers import CONFIG, Rig  # noqa: E402
from orchestrator.appclient import AppClient  # noqa: E402
from orchestrator.config import ConfigError, for_site_run, load_config  # noqa: E402
from orchestrator.failures import TerminalError  # noqa: E402
from orchestrator.run import RunAborted  # noqa: E402
from orchestrator.trace import Redactor  # noqa: E402

CAMPAIGN = "aaaaaaaa-0000-4000-8000-000000000001"     # the first campaign the fake app makes


class Args:
    def __init__(self, campaign=None, topic=None):
        self.campaign, self.topic = campaign, topic


class BudgetForOneCampaign(unittest.TestCase):
    def test_a_site_run_gets_a_share_of_the_total_and_nothing_else_changes(self):
        site = for_site_run(CONFIG)
        self.assertEqual(CONFIG.site_budget_scale, 0.34)
        self.assertEqual((site.total.steps, site.total.fetches, site.total.tokens, site.total.seconds),
                         (27, 51, 170000, 612))
        self.assertEqual(site.researcher_share, CONFIG.researcher_share)
        self.assertEqual(site.network, CONFIG.network)
        self.assertEqual(site.retry, CONFIG.retry)
        self.assertLess(site.reserve_seconds, site.total.seconds)

    def test_a_tiny_scale_never_goes_below_what_one_call_needs(self):
        tiny = for_site_run(replace(CONFIG, site_budget_scale=0.05))
        self.assertGreaterEqual(tiny.total.steps, 2)
        self.assertGreaterEqual(tiny.total.tokens, 2000)
        self.assertGreaterEqual(tiny.total.seconds, 60)
        self.assertLess(tiny.reserve_seconds, tiny.total.seconds)

    def test_the_scale_comes_from_config_yaml_and_is_checked(self):
        self.assertEqual(load_config(load_dotenv=False).site_budget_scale, 0.34)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.yaml"
            text = (Path(__file__).resolve().parents[3] / "config.yaml").read_text(encoding="utf-8")
            path.write_text(text.replace("budget_scale: 0.34", "budget_scale: 7"), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path, load_dotenv=False)

    def test_one_campaign_fits_in_that_share_and_both_workers_are_asked(self):
        rig = Rig(campaigns=1, config=for_site_run(replace(CONFIG, targets=CONFIG.targets[:1])))
        result = rig.orch.run((CAMPAIGN, "oat milk launches"))
        self.assertEqual([c[0] for c in rig.researcher.calls], ["/research"])
        self.assertEqual([c[0] for c in rig.verifier.calls], ["/verify"])
        spent = result.budget.spent()
        self.assertLessEqual(spent["tokens"], 170000)
        self.assertLessEqual(spent["steps"], 27)


class OneCampaignRun(unittest.TestCase):
    def test_only_that_campaign_is_researched_with_the_topic_it_was_given(self):
        rig = Rig(campaigns=3)
        second = rig.app.campaigns()[1]["id"]
        result = rig.orch.run((second, "reusable bottles that went viral"))
        self.assertEqual(len(result.outcomes), 1)
        self.assertEqual([c[1]["payload"]["topic"] for c in rig.researcher.calls], ["reusable bottles that went viral"])
        self.assertEqual(result.outcomes[0].campaign["id"], second)
        self.assertEqual(result.outcomes[0].topic, "reusable bottles that went viral")
        self.assertEqual(result.missing, [])

    def test_the_campaign_does_not_have_to_be_listed_in_config_yaml(self):
        rig = Rig(campaigns=1)
        rig.app._campaigns.append({"id": "bbbbbbbb-0000-4000-8000-000000000009", "title": "Somebody's own new campaign",
                                   "brand": "Own Brand"})
        result = rig.orch.run(("BBBBBBBB-0000-4000-8000-000000000009", "anything at all"))   # upper case is fine
        self.assertEqual(result.outcomes[0].campaign["title"], "Somebody's own new campaign")
        self.assertEqual(result.status, "complete")

    def test_a_campaign_that_is_not_in_the_app_stops_the_run_before_any_worker_is_called(self):
        rig = Rig(campaigns=1)
        with self.assertRaises(RunAborted) as caught:
            rig.orch.run(("cccccccc-0000-4000-8000-00000000000a", "topic"))
        self.assertIn("not found", str(caught.exception))
        self.assertEqual(rig.researcher.calls, [])
        self.assertEqual(rig.verifier.calls, [])

    def test_the_normal_run_is_unchanged(self):
        rig = Rig(campaigns=2)
        result = rig.run()
        self.assertEqual(len(result.outcomes), 2)

    def test_progress_lines_say_what_the_team_is_doing(self):
        rig = Rig(campaigns=1)
        rig.orch.run((CAMPAIGN, "a topic"))
        text = "\n".join(rig.said)
        self.assertIn("Asking the researcher", text)
        self.assertIn("Asking the verifier to check", text)


class CommandLineChecks(unittest.TestCase):
    def test_a_website_run_needs_both_a_campaign_and_a_topic(self):
        self.assertIsNone(cli.site_target(Args()))
        for args in (Args(campaign=CAMPAIGN), Args(topic="only a topic")):
            with self.assertRaises(cli.ConfigError):
                cli.site_target(args)

    def test_the_campaign_must_be_an_id_and_the_topic_must_fit_the_contract(self):
        for bad in ("../../etc/passwd", "not-an-id", CAMPAIGN + "x", "; rm -rf /"):
            with self.assertRaises(cli.ConfigError, msg=bad):
                cli.site_target(Args(campaign=bad, topic="fine"))
        for bad_topic in ("", "   ", "x" * 301):
            with self.assertRaises(cli.ConfigError, msg=repr(bad_topic)):
                cli.site_target(Args(campaign=CAMPAIGN, topic=bad_topic))

    def test_a_good_pair_is_cleaned_up(self):
        got = cli.site_target(Args(campaign=f"  {CAMPAIGN.upper()} ", topic="  oat   milk\nlaunches "))
        self.assertEqual(got, (CAMPAIGN, "oat milk launches"))

    def test_the_command_refuses_a_bad_pair_before_doing_anything(self):
        said = []
        with mock.patch.object(cli, "say", said.append), mock.patch.object(cli, "load", return_value=CONFIG):
            code = cli.main(["run", "--campaign", "nope", "--topic", "x"])
        self.assertEqual(code, 2)
        self.assertIn("Can't start", said[0])


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append((method, url.split("://", 1)[1].split("/", 1)[1], json, dict(headers or {})))
        return FakeResponse(*self.answers[(method, "/" + url.split("://", 1)[1].split("/", 1)[1])])


class NoTrace:
    def event(self, *args, **kwargs):
        pass


class TokenLogin(unittest.TestCase):
    def app(self, answers, **kwargs):
        return AppClient(NoTrace(), base_url="http://app.test", session=FakeSession(answers), sleep=lambda s: None, **kwargs)

    def test_a_token_from_the_website_is_used_as_it_is_with_no_password_login(self):
        app = self.app({("GET", "/api/auth/me"): (200, {"id": "u1", "username": "maya_makes"})}, token="site-token-123456")
        user = app.login()
        self.assertEqual(user["username"], "maya_makes")
        self.assertEqual(app.username, "maya_makes")
        self.assertEqual([c[:2] for c in app.session.calls], [("GET", "api/auth/me")])
        self.assertEqual(app.session.calls[0][3]["Authorization"], "Bearer site-token-123456")

    def test_the_environment_variable_works_the_same_way(self):
        with mock.patch.dict(os.environ, {"TRACKER_TOKEN": "env-token-abcdef"}):
            app = self.app({("GET", "/api/auth/me"): (200, {"username": "jordan_creates"})})
        self.assertEqual(app.fixed_token, "env-token-abcdef")
        app.login()
        self.assertEqual(app.session.calls[0][3]["Authorization"], "Bearer env-token-abcdef")

    def test_an_expired_token_gives_a_plain_message_about_starting_again(self):
        app = self.app({("GET", "/api/auth/me"): (401, {"error": "Your token has expired."})}, token="old-token-1234567")
        with self.assertRaises(TerminalError) as caught:
            app.login()
        self.assertIn("Start the run again from the campaign page", caught.exception.message)

    def test_without_a_token_the_password_login_still_works(self):
        answers = {("POST", "/api/auth/login"): (200, {"token": "t", "user": {"username": "NYUgrader"}})}
        with mock.patch.dict(os.environ, {"TRACKER_TOKEN": ""}):
            app = self.app(answers, username="NYUgrader", password="pw")
        self.assertEqual(app.login()["username"], "NYUgrader")
        self.assertEqual(app.session.calls[0][:2], ("POST", "api/auth/login"))

    def test_the_token_never_reaches_the_trace_log(self):
        secret = "site-token-should-stay-hidden-9876"
        with mock.patch.dict(os.environ, {"TRACKER_TOKEN": secret}):
            redactor = Redactor()
            redactor.learn_from_env()
            redactor.add(os.environ.get("TRACKER_TOKEN", ""))
        self.assertNotIn(secret, redactor.clean(f"calling the app with Bearer {secret} and also {secret}"))


class FakeAppForCommand:
    fixed_token = "site-token"
    base_url = "http://app.test"
    username = ""

    def __init__(self, *args, **kwargs):
        pass

    def save_run(self, payload):
        return {"number": 1, "counts": {"new": 2, "still": 0, "dropped": 0}}


class CommandWritesFiles(unittest.TestCase):
    def run_cli(self, argv):
        rig = Rig(campaigns=1)
        result = rig.orch.run((CAMPAIGN, "a topic")) if "--campaign" in argv else rig.run()
        said, folder = [], tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)

        class StubOrchestrator:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, only=None):
                return result

        with mock.patch.object(cli, "REPORTS", Path(folder.name)), mock.patch.object(cli, "say", said.append), \
                mock.patch.object(cli, "load", return_value=rig.config), mock.patch.object(cli, "make_trace", return_value=rig.trace), \
                mock.patch.object(cli, "make_clients", return_value={"researcher": None, "verifier": None}), \
                mock.patch.object(cli, "AppClient", FakeAppForCommand), mock.patch.object(cli, "Orchestrator", StubOrchestrator):
            code = cli.main(argv)
        return code, "\n".join(said), sorted(p.name for p in Path(folder.name).iterdir())

    def test_a_website_run_keeps_its_report_in_the_app_and_writes_no_file(self):
        code, output, files = self.run_cli(["run", "--campaign", CAMPAIGN, "--topic", "a topic"])
        self.assertEqual(code, 0)
        self.assertEqual(files, [])
        self.assertIn("as the head person who started this run", output)
        self.assertIn("saved in the app", output)
        self.assertIn("Run 1:", output)

    def test_the_normal_run_still_writes_its_report_file(self):
        code, output, files = self.run_cli(["run"])
        self.assertEqual(code, 0)
        self.assertEqual(files, ["orch-run1.md"])


if __name__ == "__main__":
    unittest.main()
