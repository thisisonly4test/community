import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scoring  # noqa: E402
from collectors import make_event  # noqa: E402

TODAY = date(2026, 10, 11)


def event(title, kind="conference", start="2026-11-20", source="confs.tech", **kwargs):
    return make_event(
        source=source,
        native_id=title,
        title=title,
        url="https://example.org",
        kind=kind,
        start=date.fromisoformat(start),
        end=date.fromisoformat(kwargs.pop("end", start)),
        **kwargs,
    )


def score(item):
    return scoring.score_event(item, TODAY)["score"]


class DomainTest(unittest.TestCase):
    def test_domains(self):
        self.assertEqual(scoring.detect_domains(event("BSides Paris")), [scoring.CYBER])
        self.assertEqual(scoring.detect_domains(event("ROSCon France")), [scoring.ROBOTICS])
        self.assertEqual(scoring.detect_domains(event("PyCon FR")), [])
        self.assertIn(scoring.TECH, scoring.detect_domains(event("Python developers day")))

    def test_ambiguous_security_needs_tech(self):
        self.assertEqual(scoring.detect_domains(event("Energy and food security")), [])
        self.assertIn(scoring.CYBER, scoring.detect_domains(event("AI Security Meetup")))

    def test_hackers_is_not_cyber(self):
        self.assertNotIn(scoring.CYBER, scoring.detect_domains(event("Indie Hackers Lille")))

    def test_format_implies_domain(self):
        self.assertEqual(scoring.detect_domains(event("Knight Hacks", kind="hackathon")), [scoring.TECH])
        self.assertEqual(scoring.detect_domains(event("FCSC", kind="ctf")), [scoring.CYBER])

    def test_off_topic_scores_zero(self):
        item = scoring.score_event(event("Soirée jeux de société", kind="meetup", city="Paris", country="France"), TODAY)
        self.assertEqual(item["score"], 0)


class LocationRuleTest(unittest.TestCase):
    def test_location_class(self):
        self.assertEqual(scoring.location_class(event("x", city="Paris", country="France")), "france")
        self.assertEqual(scoring.location_class(event("x", city="Lyon")), "france")
        self.assertEqual(scoring.location_class(event("x", city="Berlin", country="Germany")), "europe")
        self.assertEqual(scoring.location_class(event("x", city="Austin", country="USA")), "world")
        self.assertEqual(scoring.location_class(event("x", online=True)), "online")

    def test_small_events_abroad_are_excluded(self):
        paris = score(event("AI Hackathon", kind="hackathon", city="Paris", country="France"))
        online = score(event("AI Hackathon", kind="hackathon", online=True))
        boston = score(event("AI Hackathon", kind="hackathon", city="Boston", country="USA"))
        self.assertGreater(paris, online)
        self.assertGreater(online, boston)
        self.assertLess(boston, 40)

    def test_flagship_conference_abroad_is_kept(self):
        item = event(
            "S&P 2027 — IEEE Symposium on Security and Privacy", kind="academic", source="sec-deadlines",
            start="2027-05-17", city="Montreal", country="Canada", topics=["security"], flagship=True,
        )
        self.assertGreaterEqual(score(item), 60)


class PenaltyTest(unittest.TestCase):
    def test_business_meetup_is_penalized(self):
        dev = score(event("Agents & APIs Paris Developer Meetup", kind="meetup", city="Paris", country="France"))
        biz = score(event("IA & Finance : afterwork networking", kind="meetup", city="Paris", country="France"))
        self.assertGreater(dev, biz)

    def test_generic_conference_is_penalized(self):
        generic = score(event("International Conference on Cybersecurity and AI", country="India", city="Delhi"))
        named = score(event("Cybersecurity Days", country="India", city="Delhi"))
        self.assertLess(generic, named)

    def test_paid_masterclass(self):
        plain = score(event("Kubernetes Days", online=True))
        paid = score(event("Kubernetes Days", online=True, format="masterclass"))
        self.assertLess(paid, plain)

    def test_ctf_weight(self):
        strong = score(event("Big CTF", kind="ctf", online=True, weight=90.0))
        unknown = score(event("New CTF", kind="ctf", online=True, weight=0.0))
        self.assertGreater(strong, unknown)
        self.assertLess(unknown, 40)

    def test_open_cfp_bonus(self):
        without = score(event("DevSecOps Days", city="Paris", country="France"))
        with_cfp = score(event("DevSecOps Days", city="Paris", country="France", cfp_end="2026-11-01"))
        self.assertGreater(with_cfp, without)
        self.assertIn("appel à contributions", " ".join(scoring.score_event(
            event("DevSecOps Days", cfp_end="2026-11-01"), TODAY)["reasons"]))


class DedupeTest(unittest.TestCase):
    def test_merges_sources(self):
        a = event("Devoxx France 2027", start="2027-04-14", source="dev.events")
        b = event("Devoxx France", start="2027-04-14", source="confs.tech")
        merged = scoring.dedupe([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "confs.tech")
        self.assertEqual(merged[0]["sources"], ["confs.tech", "dev.events"])

    def test_different_dates_kept(self):
        a = event("Meetup Rust", start="2026-10-20", source="Luma")
        b = event("Meetup Rust", start="2026-11-20", source="Luma")
        self.assertEqual(len(scoring.dedupe([a, b])), 2)


class SelectionTest(unittest.TestCase):
    def test_diversity_limits_one_kind(self):
        ctfs = [dict(event(f"CTF {i}", kind="ctf"), score=70, primary_domain=scoring.CYBER) for i in range(5)]
        conf = dict(event("RobotConf"), score=60, primary_domain=scoring.ROBOTICS)
        selected = scoring.select_diverse(ctfs + [conf], limit=3)
        self.assertIn(conf["id"], [item["id"] for item in selected])

    def test_limit(self):
        items = [dict(event(f"E{i}"), score=50, primary_domain=scoring.TECH) for i in range(10)]
        self.assertEqual(len(scoring.select_diverse(items, limit=4)), 4)


if __name__ == "__main__":
    unittest.main()
