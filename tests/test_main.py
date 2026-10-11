import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import scoring  # noqa: E402
from collectors import make_event  # noqa: E402

TODAY = date(2026, 10, 11)


def event(title, start="2026-11-20", **kwargs):
    item = make_event(
        source=kwargs.pop("source", "confs.tech"),
        native_id=title,
        title=title,
        url="https://example.org",
        kind=kwargs.pop("kind", "conference"),
        start=date.fromisoformat(start),
        **kwargs,
    )
    return scoring.score_event(item, TODAY)


class SeenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(main, "SEEN_FILE", os.path.join(self.tmp.name, "data", "seen.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_missing_file(self):
        self.assertEqual(main.load_seen(), {})

    def test_mark_and_prune(self):
        seen = {"old:1": "2026-01-01"}
        with mock.patch("builtins.print"), mock.patch.object(main, "date") as fake_date:
            fake_date.today.return_value = TODAY
            fake_date.fromisoformat = date.fromisoformat
            main.mark_seen(seen, [event("BSides Paris", city="Paris", country="France")])

        with open(main.SEEN_FILE, encoding="utf-8") as file:
            stored = json.load(file)

        self.assertNotIn("old:1", stored)
        self.assertIn("confs.tech:BSides Paris", stored)
        self.assertIn("bsides paris|2026-11-20", stored)

    def test_is_seen_by_dedupe_key(self):
        item = event("Devoxx France", start="2027-04-14", source="dev.events")
        self.assertTrue(main.is_seen(item, {"devoxx france|2027-04-14": "2027-04-16"}))
        self.assertFalse(main.is_seen(item, {}))


class EmbedTest(unittest.TestCase):
    def test_embed_content(self):
        item = event(
            "Pass the SALT", start="2027-07-01", end=date(2027, 7, 3), city="Lille", country="France",
            topics=["security"], cfp_end="2026-11-01",
        )
        embed = main.build_embed(item, TODAY)
        self.assertEqual(embed["title"], "Pass the SALT")
        self.assertEqual(embed["author"]["name"], "🎤 CONFÉRENCE  ·  Cybersécurité")
        self.assertEqual(embed["color"], main.KIND_COLORS["conference"])
        self.assertIn("📍 **Lille**, France 🇫🇷", embed["description"])
        self.assertIn("📅 **jeu. 1 juil. 2027** → sam. 3 juil. 2027", embed["description"])
        self.assertIn("Appel à contributions avant le **1 nov.**", embed["description"])
        # Lieu, thème et CFP sont déjà visibles : seule la notoriété reste en citation.
        self.assertTrue(embed["description"].endswith("> événement reconnu"))
        self.assertIn("score", embed["footer"]["text"])

    def test_reasons_quote(self):
        item = event("BSides Paris", city="Paris", country="France")
        self.assertIn("> événement reconnu", main.build_embed(item, TODAY)["description"])

    def test_dates_wording(self):
        self.assertIn("*demain*", main.format_dates({"start": "2026-10-12", "end": "2026-10-12"}, TODAY))
        self.assertIn("*déjà ouvert*", main.format_dates({"start": "2026-10-01", "end": "2026-11-12"}, TODAY))
        # Année masquée quand c'est l'année en cours.
        self.assertEqual(main.format_day("2026-10-12", TODAY), "lun. 12 oct.")

    def test_sections(self):
        items = [
            event("Online Hackathon", kind="hackathon", online=True, source="Devpost"),
            event("CCS", kind="academic", city="The Hague", country="Netherlands", source="sec-deadlines"),
            event("BSides Paris", city="Paris", country="France"),
            event("Robot CTF", kind="ctf", online=True, source="CTFtime"),
        ]
        payloads = main.build_payloads(items, TODAY)
        self.assertEqual(len(payloads), 3)
        self.assertIn("## 📡 Community Radar", payloads[0]["content"])
        self.assertIn("### 🇫🇷 En France", payloads[0]["content"])
        self.assertEqual(payloads[1]["content"], "### 🌍 Grandes conférences")
        self.assertEqual(payloads[2]["content"], "### 🌐 En ligne")
        self.assertEqual(len(payloads[2]["embeds"]), 2)

    def test_payloads_respect_discord_limits(self):
        items = [event(f"Cyber conference {i}" + " x" * 80, city="Paris", country="France") for i in range(25)]
        payloads = main.build_payloads(items, TODAY)
        self.assertEqual(sum(len(p["embeds"]) for p in payloads), 25)
        for payload in payloads:
            self.assertLessEqual(len(payload["embeds"]), main.DISCORD_MAX_EMBEDS)
            self.assertLessEqual(sum(main.embed_size(e) for e in payload["embeds"]), 6000)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(main, "SEEN_FILE", os.path.join(self.tmp.name, "data", "seen.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def raw(self, title, **kwargs):
        return make_event(
            source=kwargs.pop("source", "Luma"), native_id=title, title=title, url="https://x",
            # main() utilise la vraie date du jour : l'événement reste toujours à venir.
            kind=kwargs.pop("kind", "meetup"), start=date.today() + timedelta(days=20), **kwargs,
        )

    def run_main(self, events):
        sent = []
        with mock.patch.object(main, "fetch_all", return_value=(events, [])), \
                mock.patch.object(main, "post_discord", side_effect=sent.append), \
                mock.patch("builtins.print"):
            main.main()
        return sent

    def test_sends_relevant_once(self):
        events = [
            self.raw("AI Security Paris Meetup", city="Paris", country="France"),
            self.raw("Soirée jeux de société", city="Paris", country="France"),
        ]
        sent = self.run_main(events)
        self.assertEqual(len(sent), 1)
        titles = [embed["title"] for embed in sent[0]["embeds"]]
        self.assertEqual(titles, ["AI Security Paris Meetup"])

        # Deuxième run : déjà envoyé, rien ne repart.
        again = [
            self.raw("AI Security Paris Meetup", city="Paris", country="France"),
            self.raw("Soirée jeux de société", city="Paris", country="France"),
        ]
        self.assertEqual(self.run_main(again), [])

    def test_nothing_relevant_sends_nothing(self):
        self.assertEqual(self.run_main([self.raw("Yoga brunch", city="Paris", country="France")]), [])


if __name__ == "__main__":
    unittest.main()
