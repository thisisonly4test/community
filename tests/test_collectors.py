import json
import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collectors  # noqa: E402

TODAY = date(2026, 10, 11)


class ParseDateRangeTest(unittest.TestCase):
    def test_formats(self):
        cases = {
            "Oct 20 - Dec 21, 2026": (date(2026, 10, 20), date(2026, 12, 21)),
            "Dec 28, 2026 - Jan 04, 2027": (date(2026, 12, 28), date(2027, 1, 4)),
            "November 15-19": (date(2026, 11, 15), date(2026, 11, 19)),
            "Oct 30 - Nov 3": (date(2026, 10, 30), date(2026, 11, 3)),
            "May 24 - 28, 2027": (date(2027, 5, 24), date(2027, 5, 28)),
            "December 10, 2026": (date(2026, 12, 10), date(2026, 12, 10)),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(collectors.parse_date_range(text, 2026), expected)

    def test_year_wraps_without_explicit_start_year(self):
        self.assertEqual(
            collectors.parse_date_range("Dec 28 - Jan 4, 2027"),
            (date(2026, 12, 28), date(2027, 1, 4)),
        )

    def test_unreadable(self):
        self.assertEqual(collectors.parse_date_range("TBA", 2026), (None, None))
        self.assertEqual(collectors.parse_date_range("", 2026), (None, None))


class SourceParsersTest(unittest.TestCase):
    def test_confs_tech(self):
        events = collectors.parse_confs_tech(
            [{
                "name": "Pass the SALT",
                "url": "https://pass-the-salt.org",
                "startDate": "2027-07-01",
                "endDate": "2027-07-03",
                "city": "Lille",
                "country": "France",
                "online": False,
                "cfpEndDate": "2027-03-01",
            }],
            "security",
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "conference")
        self.assertEqual(event["start"], "2027-07-01")
        self.assertEqual(event["country"], "France")
        self.assertEqual(event["topics"], ["security"])
        self.assertEqual(event["cfp_end"], "2027-03-01")

    def test_dev_events_rss(self):
        xml = """<?xml version="1.0"?><rss><channel>
        <item><title><![CDATA[DevFest Paris]]></title>
        <link>https://dev.events/conferences/devfest-paris</link>
        <guid>devfest-paris</guid>
        <description><![CDATA[DevFest Paris is happening on February 25, 2027 in Paris, France, Europe. More information: x]]></description>
        <category>Cloud</category></item>
        <item><title><![CDATA[Clean Code Masterclass]]></title>
        <link>https://dev.events/conferences/clean</link><guid>clean</guid>
        <description><![CDATA[Clean Code Masterclass is happening on December 10, 2026, Online. More information: x]]></description>
        <category>masterclass</category></item>
        </channel></rss>"""
        events = collectors.parse_dev_events(xml)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["city"], "Paris")
        self.assertEqual(events[0]["country"], "France")
        self.assertFalse(events[0]["online"])
        self.assertTrue(events[1]["online"])
        self.assertEqual(events[1]["format"], "masterclass")

    def test_devpost(self):
        events = collectors.parse_devpost(
            [{
                "id": 1,
                "title": "Robo Hacks",
                "url": "https://robo.devpost.com/",
                "displayed_location": {"location": "Online"},
                "submission_period_dates": "Oct 20 - Dec 21, 2026",
                "themes": [{"name": "Machine Learning/AI"}],
                "prize_amount": "$<span data-currency-value>12,500</span>",
                "registrations_count": 640,
                "organization_name": "Robo Inc",
            }],
            today=TODAY,
        )
        event = events[0]
        self.assertEqual(event["id"], "Devpost:1")
        self.assertTrue(event["online"])
        self.assertEqual(event["prize"], "$12,500")
        self.assertEqual(event["prize_value"], 12500)
        self.assertEqual(event["end"], "2026-12-21")

    def test_mlh(self):
        data = {"props": {"upcomingEvents": [{
            "id": "abc", "name": "HackParis", "startsAt": "2026-11-07T09:00:00Z",
            "endsAt": "2026-11-08T18:00:00Z", "formatType": "physical",
            "websiteUrl": "https://hackparis.fr", "venueAddress": {"city": "Paris", "country": "FR"},
            "customFields": {"hackathon_focus": ["Hardware"]},
        }]}}
        page = f'<html><script type="application/json">{json.dumps(data)}</script></html>'
        event = collectors.parse_mlh(page)[0]
        self.assertEqual(event["kind"], "hackathon")
        self.assertEqual(event["country"], "FR")
        self.assertFalse(event["online"])

    def test_ctftime(self):
        event = collectors.parse_ctftime([{
            "id": 42, "title": "FCSC", "start": "2027-04-01T10:00:00+00:00",
            "finish": "2027-04-11T10:00:00+00:00", "onsite": False, "weight": 30,
            "ctftime_url": "https://ctftime.org/event/42/", "format": "Jeopardy",
            "restrictions": "Open",
        }])[0]
        self.assertEqual(event["kind"], "ctf")
        self.assertTrue(event["online"])
        self.assertEqual(event["weight"], 30.0)

    def test_luma_keeps_country_code(self):
        event = collectors.parse_luma([{"event": {
            "api_id": "evt-1", "name": "Meetup Rust", "start_at": "2026-10-20T17:00:00.000Z",
            "end_at": "2026-10-20T20:00:00.000Z", "url": "abc", "location_type": "offline",
            "geo_address_info": {"city": "Lyon", "country": "France", "country_code": "FR"},
        }}])[0]
        self.assertEqual(event["url"], "https://lu.ma/abc")
        self.assertEqual(event["country"], "France")

    def test_sec_deadlines(self):
        items = [
            {"name": "CCS", "year": 2026, "date": "November 15-19", "description": "ACM CCS",
             "place": "The Hague, The Netherlands", "link": "https://ccs", "deadline": ["2026-01-14 23:59"],
             "tags": ["SEC", "CONF", "ASTAR", "TOP4"]},
            {"name": "ICSE", "year": 2027, "date": "April 25-May 1", "place": "Dublin, Ireland",
             "link": "https://icse", "tags": ["CONF"]},
            {"name": "Workshop", "year": 2026, "date": "Nov 1", "tags": ["SEC", "WORKSHOP"]},
        ]
        events = collectors.parse_sec_deadlines(items, today=TODAY)
        self.assertEqual(len(events), 2)
        self.assertTrue(events[0]["flagship"])
        self.assertEqual(events[0]["start"], "2026-11-15")
        self.assertIsNone(events[0]["cfp_end"])
        self.assertEqual(events[1]["topics"], ["software"])

    def test_ai_deadlines_next_submission(self):
        events = collectors.parse_ai_deadlines(
            [{"title": "ICRA", "year": 2027, "id": "icra27", "start": "2027-05-24", "end": "2027-05-28",
              "city": "Seoul", "country": "South Korea", "hindex": 129, "tags": ["robotics"],
              "deadlines": [
                  {"type": "submission", "date": "2026-09-15 23:59:59"},
                  {"type": "submission", "date": "2026-11-01 23:59:59"},
                  {"type": "camera_ready", "date": "2026-10-20 23:59:59"},
              ]}],
            today=TODAY,
        )
        self.assertEqual(events[0]["cfp_end"], "2026-11-01")
        self.assertTrue(events[0]["flagship"])


class UpcomingTest(unittest.TestCase):
    def event(self, kind, start, end):
        return collectors.make_event(
            source="x", native_id=1, title="t", url="u", kind=kind,
            start=date.fromisoformat(start), end=date.fromisoformat(end),
        )

    def test_rules(self):
        self.assertTrue(collectors.is_upcoming(self.event("conference", "2026-10-11", "2026-10-12"), TODAY))
        self.assertFalse(collectors.is_upcoming(self.event("conference", "2026-10-10", "2026-10-12"), TODAY))
        self.assertFalse(collectors.is_upcoming(self.event("conference", "2028-01-01", "2028-01-02"), TODAY))
        # Hackathon déjà ouvert mais encore rejoignable.
        self.assertTrue(collectors.is_upcoming(self.event("hackathon", "2026-09-01", "2026-11-01"), TODAY))
        self.assertFalse(collectors.is_upcoming(self.event("hackathon", "2026-09-01", "2026-10-14"), TODAY))

    def test_refine_kind(self):
        event = self.event("meetup", "2026-10-20", "2026-10-20")
        event["title"] = "Build Agents Hackathon @42Paris"
        self.assertEqual(collectors.refine_kind(event)["kind"], "hackathon")


class FetchAllTest(unittest.TestCase):
    def test_one_source_down_is_tolerated(self):
        ok = collectors.make_event(
            source="ok", native_id=1, title="t", url="u", kind="conference", start=date(2026, 12, 1),
        )

        def broken():
            raise OSError("boom")

        with mock.patch("builtins.print"):
            events, failures = collectors.fetch_all([("ok", lambda: [ok]), ("ko", broken)], today=TODAY)
        self.assertEqual(len(events), 1)
        self.assertEqual(failures, ["ko"])

    def test_all_sources_down_raises(self):
        def broken():
            raise OSError("boom")

        with mock.patch("builtins.print"), self.assertRaises(RuntimeError):
            collectors.fetch_all([("a", broken), ("b", broken)], today=TODAY)


if __name__ == "__main__":
    unittest.main()
