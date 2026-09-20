import unittest
from unittest import mock

from tests import helpers
import web_search


class FakeResponse:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": self._results}


def result(url, title="t"):
    return {"title": title, "url": url, "content": "snippet"}


class TrustLabels(unittest.TestCase):
    def setUp(self):
        self._before = (web_search._base_url, web_search._trusted_domains)
        self.addCleanup(lambda: setattr(web_search, "_base_url", self._before[0]))
        self.addCleanup(lambda: setattr(web_search, "_trusted_domains", self._before[1]))
        web_search.configure("http://searx.local:8080/", trusted_domains=["nfrealmusic.com", " WWW.Example.org "])

    def test_labels(self):
        trust = web_search.trust_of
        self.assertEqual(trust("https://www.youtube.com/watch?v=1"), "user-uploaded")
        self.assertEqual(trust("https://m.youtube.com/watch?v=1"), "user-uploaded")  # subdomain
        self.assertEqual(trust("https://old.reddit.com/r/x"), "user-uploaded")
        self.assertEqual(trust("https://nfrealmusic.com/tour"), "trusted")
        self.assertEqual(trust("https://shop.nfrealmusic.com/"), "trusted")
        self.assertEqual(trust("https://example.org/a"), "trusted")  # configured with www./caps/padding
        self.assertEqual(trust("https://randomblog.net/post"), "unverified")
        self.assertEqual(trust("not a url"), "unverified")

    def test_lookalike_domains_are_not_trusted(self):
        self.assertEqual(web_search.trust_of("https://nfrealmusic.com.evil.io/"), "unverified")
        self.assertEqual(web_search.trust_of("https://notyoutube.com/"), "unverified")

    def test_a_trusted_domain_wins_over_the_user_uploaded_list(self):
        web_search.configure("http://x", trusted_domains=["youtube.com"])
        self.assertEqual(web_search.trust_of("https://youtube.com/@official"), "trusted")


class Search(unittest.TestCase):
    def setUp(self):
        self._before = (web_search._base_url, web_search._trusted_domains)
        self.addCleanup(lambda: setattr(web_search, "_base_url", self._before[0]))
        self.addCleanup(lambda: setattr(web_search, "_trusted_domains", self._before[1]))

    def run_search(self, results, trusted=()):
        web_search.configure("http://searx.local:8080", trusted_domains=trusted)
        with mock.patch.object(web_search.httpx, "get", return_value=FakeResponse(results)):
            return web_search.search("q")

    def test_unconfigured_returns_nothing_without_a_request(self):
        web_search._base_url = None
        with mock.patch.object(web_search.httpx, "get") as get:
            self.assertEqual(web_search.search("q"), [])
            get.assert_not_called()
        self.assertFalse(web_search.read_active())

    def test_ranking_trusted_first_user_uploaded_last_order_kept_within_groups(self):
        found = self.run_search(
            [
                result("https://youtube.com/a"),
                result("https://blog-one.net/x"),
                result("https://nfrealmusic.com/y"),
                result("https://blog-two.net/z"),
                result("https://reddit.com/r"),
            ],
            trusted=["nfrealmusic.com"],
        )
        self.assertEqual(
            [r["domain"] for r in found],
            ["nfrealmusic.com", "blog-one.net", "blog-two.net", "youtube.com", "reddit.com"],
        )
        self.assertEqual([r["trust"] for r in found], ["trusted", "unverified", "unverified", "user-uploaded", "user-uploaded"])

    def test_a_trusted_result_far_down_still_makes_the_cut(self):
        junk = [result(f"https://junk{i}.net/") for i in range(20)]
        found = self.run_search(junk + [result("https://nfrealmusic.com/late")], trusted=["nfrealmusic.com"])
        self.assertEqual(len(found), web_search.MAX_RESULTS)
        self.assertEqual(found[0]["domain"], "nfrealmusic.com")

    def test_request_failure_degrades_to_no_results(self):
        web_search.configure("http://searx.local:8080")
        with mock.patch.object(web_search.httpx, "get", side_effect=RuntimeError("down")):
            self.assertEqual(web_search.search("q"), [])

    def test_toggle_defaults_off_and_persists(self):
        with helpers.isolated_state():
            web_search.configure("http://searx.local:8080")
            self.assertFalse(web_search.read_active())
            web_search.set_active(True)
            self.assertTrue(web_search.read_active())


if __name__ == "__main__":
    unittest.main()
