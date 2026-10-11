"""
Collecte des événements à venir.

Chaque source renvoie une liste d'événements normalisés (voir `make_event`).
Une source en panne n'arrête pas les autres : `fetch_all` remonte l'erreur
seulement si toutes les sources ont échoué.
"""

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

USER_AGENT = "Mozilla/5.0 (compatible; Community-Radar/1.0)"
REQUEST_DELAY = 0.5

# Événements trop lointains : les dates bougent encore et ils reviendront.
HORIZON_DAYS = 365

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# confs.tech : un fichier JSON par thème et par année.
CONFS_TECH_TOPICS = [
    "security", "iot", "general", "data", "devops",
    "opensource", "networking", "python", "rust", "sre",
]

# Grandes conférences académiques (dépôt maintenu par Hugging Face).
AI_DEADLINES_FILES = [
    "icra", "iros", "corl", "rss",
    "neurips", "icml", "iclr", "cvpr",
]

# Luma ne propose pas de recherche par pays : on interroge autour de villes.
LUMA_CITIES = [
    ("Paris", 48.8566, 2.3522, 8),
    ("Lyon", 45.7640, 4.8357, 3),
    ("Toulouse", 43.6047, 1.4442, 3),
    ("Lille", 50.6292, 3.0573, 2),
    ("Bordeaux", 44.8378, -0.5792, 2),
    ("Nantes", 47.2184, -1.5536, 2),
    ("Rennes", 48.1173, -1.6778, 2),
    ("Grenoble", 45.1885, 5.7245, 2),
    ("Marseille", 43.2965, 5.3698, 2),
    ("Sophia Antipolis", 43.6163, 7.0552, 2),
]


def make_event(
    *,
    source,
    native_id,
    title,
    url,
    kind,
    start,
    end=None,
    city="",
    country="",
    online=False,
    topics=None,
    description="",
    **extra,
):
    """
    kind : conference | academic | hackathon | ctf | meetup.
    start / end : objets `date`.
    """
    event = {
        "id": f"{source}:{native_id}",
        "source": source,
        "title": " ".join((title or "").split()),
        "url": url,
        "kind": kind,
        "start": start.isoformat(),
        "end": (end or start).isoformat(),
        "city": (city or "").strip(),
        "country": (country or "").strip(),
        "online": bool(online),
        "topics": [topic for topic in (topics or []) if topic],
        "description": " ".join(html.unescape(description or "").split()),
    }
    event.update(extra)
    return event


# ---------------------------------------------------------------- HTTP


def http_get(url, accept="application/json"):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def http_json(url):
    return json.loads(http_get(url))


# ---------------------------------------------------------------- dates


def _month(token):
    return MONTHS.get(token.strip().lower()[:3])


def parse_iso_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_date_range(text, default_year=None):
    """
    Lit les plages de dates écrites à l'anglaise :
      "Oct 20 - Dec 21, 2026", "Dec 28, 2026 - Jan 04, 2027",
      "November 15-19", "Oct 30 - Nov 3", "May 24 - 28, 2027",
      "December 10, 2026".
    Renvoie (début, fin) ou (None, None).
    """
    if not text:
        return None, None

    text = text.replace("–", "-").replace("—", "-")
    parts = re.findall(r"([A-Za-z]{3,})\.?\s+(\d{1,2})(?:,?\s*(\d{4}))?|(\d{1,2})(?:,?\s*(\d{4}))?", text)

    points = []
    for month_name, day, year, bare_day, bare_year in parts:
        if month_name:
            month = _month(month_name)
            if not month:
                continue
            points.append([month, int(day), int(year) if year else None])
        elif bare_day and points:
            # "November 15-19" : le second jour hérite du mois précédent.
            points.append([points[-1][0], int(bare_day), int(bare_year) if bare_year else None])
        if len(points) == 2:
            break

    if not points:
        return None, None

    # Année : celle écrite sur le point, sinon celle du point suivant, sinon défaut.
    trailing = re.findall(r"\b(20\d{2})\b", text)
    fallback = int(trailing[-1]) if trailing else default_year
    if fallback is None:
        return None, None

    if len(points) == 1:
        points.append(list(points[0]))

    start_year = points[0][2] or points[1][2] or fallback
    end_year = points[1][2] or fallback
    # "Dec 28 - Jan 4, 2027" : le début est l'année d'avant.
    if not points[0][2] and (points[0][0], points[0][1]) > (points[1][0], points[1][1]):
        start_year = end_year - 1

    try:
        start = date(start_year, points[0][0], points[0][1])
        end = date(end_year, points[1][0], points[1][1])
    except ValueError:
        return None, None

    if end < start:
        end = start
    return start, end


# ---------------------------------------------------------------- confs.tech


def parse_confs_tech(items, topic):
    events = []
    for item in items:
        start = parse_iso_date(item.get("startDate"))
        if not start:
            continue
        end = parse_iso_date(item.get("endDate")) or start
        cfp_end = parse_iso_date(item.get("cfpEndDate"))
        country = item.get("country") or ""
        online = bool(item.get("online")) or country.lower() == "online"
        events.append(
            make_event(
                source="confs.tech",
                native_id=f"{item.get('url', '')}|{start.isoformat()}",
                title=item.get("name", ""),
                url=item.get("url", ""),
                kind="conference",
                start=start,
                end=end,
                city=item.get("city", ""),
                country="" if country.lower() == "online" else country,
                online=online,
                topics=[topic],
                cfp_end=cfp_end.isoformat() if cfp_end else None,
                cfp_url=item.get("cfpUrl"),
            )
        )
    return events


def fetch_confs_tech(today=None):
    today = today or date.today()
    events = {}
    for year in (today.year, today.year + 1):
        for topic in CONFS_TECH_TOPICS:
            url = (
                "https://raw.githubusercontent.com/tech-conferences/"
                f"conference-data/main/conferences/{year}/{topic}.json"
            )
            try:
                # Une conférence peut figurer dans plusieurs thèmes : on fusionne.
                for event in parse_confs_tech(http_json(url), topic):
                    known = events.setdefault(event["id"], event)
                    if topic not in known["topics"]:
                        known["topics"].append(topic)
            except urllib.error.HTTPError as error:
                # Tous les thèmes n'existent pas chaque année.
                if error.code != 404:
                    raise
            time.sleep(REQUEST_DELAY / 5)
    return list(events.values())


# ---------------------------------------------------------------- dev.events


DEV_EVENTS_LOCATION = re.compile(
    r"is happening on ([A-Za-z]+ \d{1,2}, \d{4})(?:,| in)\s*(.+?)\.\s*More information",
    re.S,
)


def parse_dev_events(xml_text):
    root = ET.fromstring(xml_text)
    events = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        guid = item.findtext("guid") or link
        description = item.findtext("description") or ""
        categories = [node.text or "" for node in item.findall("category")]

        match = DEV_EVENTS_LOCATION.search(description)
        if not match:
            continue
        start, _ = parse_date_range(match.group(1))
        if not start:
            continue

        place = match.group(2).strip()
        online = "online" in place.lower()
        # "Paris, France, Europe" / "Online" / "Berlin, Germany, Europe and Online"
        pieces = [piece.strip() for piece in re.split(r",| and ", place) if piece.strip()]
        pieces = [piece for piece in pieces if piece.lower() != "online"]
        city = pieces[0] if len(pieces) >= 3 else ""
        country = pieces[1] if len(pieces) >= 3 else (pieces[0] if len(pieces) == 2 else "")

        kind = "conference"
        if any(category.lower() in {"hackathon"} for category in categories):
            kind = "hackathon"

        events.append(
            make_event(
                source="dev.events",
                native_id=guid,
                title=title,
                url=link,
                kind=kind,
                start=start,
                city=city,
                country=country,
                online=online,
                topics=categories,
                format=next(
                    (c for c in categories if c.lower() in {"masterclass", "workshop", "meetup", "webinar"}),
                    None,
                ),
            )
        )
    return events


def fetch_dev_events():
    return parse_dev_events(http_get("https://dev.events/rss.xml", accept="application/rss+xml"))


# ---------------------------------------------------------------- Devpost


def parse_devpost(hackathons, today=None):
    today = today or date.today()
    events = []
    for item in hackathons:
        start, end = parse_date_range(item.get("submission_period_dates", ""), today.year)
        if not start:
            continue

        location = ((item.get("displayed_location") or {}).get("location") or "").strip()
        online = location.lower() == "online"

        prize_text = re.sub(r"<[^>]+>", "", item.get("prize_amount") or "")
        prize_digits = re.sub(r"[^\d]", "", prize_text)

        events.append(
            make_event(
                source="Devpost",
                native_id=item.get("id"),
                title=item.get("title", ""),
                url=item.get("url", ""),
                kind="hackathon",
                start=start,
                end=end,
                city="" if online else location,
                country="",
                online=online,
                topics=[theme.get("name", "") for theme in item.get("themes", [])],
                organizer=(item.get("organization_name") or "").strip(),
                prize=prize_text.strip() or None,
                prize_value=int(prize_digits) if prize_digits else 0,
                registrations=item.get("registrations_count") or 0,
                open_state=item.get("open_state"),
            )
        )
    return events


def fetch_devpost(max_pages=6):
    events = []
    for page in range(1, max_pages + 1):
        url = (
            "https://devpost.com/api/hackathons?"
            "status[]=upcoming&status[]=open&per_page=40&page=" + str(page)
        )
        data = http_json(url)
        hackathons = data.get("hackathons", [])
        events += parse_devpost(hackathons)
        if len(hackathons) < 40:
            break
        time.sleep(REQUEST_DELAY)
    return events


# ---------------------------------------------------------------- MLH


def parse_mlh(page_html):
    match = re.search(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        page_html,
        re.S,
    )
    if not match:
        raise ValueError("MLH : données JSON introuvables dans la page")

    props = json.loads(match.group(1)).get("props", {})
    events = []
    for item in props.get("upcomingEvents", []):
        start = parse_iso_date(item.get("startsAt"))
        if not start:
            continue
        venue = item.get("venueAddress") or {}
        format_type = item.get("formatType") or ""
        focus = (item.get("customFields") or {}).get("hackathon_focus") or []
        events.append(
            make_event(
                source="MLH",
                native_id=item.get("id") or item.get("slug"),
                title=item.get("name", ""),
                url=item.get("websiteUrl") or f"https://www.mlh.com{item.get('url', '')}",
                kind="hackathon",
                start=start,
                end=parse_iso_date(item.get("endsAt")) or start,
                city=venue.get("city", ""),
                country=venue.get("country", ""),
                online=format_type in {"digital", "hybrid"},
                topics=list(focus),
                region=item.get("region"),
            )
        )
    return events


def fetch_mlh(today=None):
    today = today or date.today()
    # Une saison MLH va de l'été à l'été suivant : "2027" couvre 2026-2027.
    seasons = (today.year + 1, today.year) if today.month >= 7 else (today.year, today.year + 1)
    events = []
    for season in seasons:
        try:
            events += parse_mlh(http_get(f"https://www.mlh.com/seasons/{season}/events", accept="text/html"))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        time.sleep(REQUEST_DELAY)
    return events


# ---------------------------------------------------------------- CTFtime


def parse_ctftime(items):
    events = []
    for item in items:
        start = parse_iso_date(item.get("start"))
        if not start:
            continue
        onsite = bool(item.get("onsite"))
        location = (item.get("location") or "").strip()
        events.append(
            make_event(
                source="CTFtime",
                native_id=item.get("id"),
                title=item.get("title", ""),
                url=item.get("ctftime_url") or item.get("url", ""),
                kind="ctf",
                start=start,
                end=parse_iso_date(item.get("finish")) or start,
                city=location if onsite else "",
                country="",
                online=not onsite,
                topics=["CTF", item.get("format") or ""],
                description=item.get("description", "")[:600],
                weight=float(item.get("weight") or 0),
                participants=item.get("participants") or 0,
                restrictions=item.get("restrictions"),
                ctf_format=item.get("format"),
            )
        )
    return events


def fetch_ctftime(days=120):
    now = int(datetime.now(timezone.utc).timestamp())
    url = (
        "https://ctftime.org/api/v1/events/?limit=300"
        f"&start={now}&finish={now + days * 86400}"
    )
    return parse_ctftime(http_json(url))


# ---------------------------------------------------------------- Luma


def parse_luma(entries):
    events = []
    for entry in entries:
        item = entry.get("event") or {}
        start = parse_iso_date(item.get("start_at"))
        if not start:
            continue
        geo = item.get("geo_address_info") or {}
        online = item.get("location_type") == "online"
        slug = item.get("url") or ""
        events.append(
            make_event(
                source="Luma",
                native_id=item.get("api_id") or slug,
                title=item.get("name", ""),
                url=f"https://lu.ma/{slug}",
                kind="meetup",
                start=start,
                end=parse_iso_date(item.get("end_at")) or start,
                city=geo.get("city", ""),
                country="France" if geo.get("country_code") == "FR" else geo.get("country", ""),
                online=online,
                topics=[],
                guests=entry.get("guest_count") or 0,
            )
        )
    return events


def fetch_luma(cities=LUMA_CITIES):
    events = {}
    for _, latitude, longitude, max_pages in cities:
        cursor = None
        for _ in range(max_pages):
            params = {
                "latitude": latitude,
                "longitude": longitude,
                "pagination_limit": 50,
            }
            if cursor:
                params["pagination_cursor"] = cursor
            data = http_json(
                "https://api.lu.ma/discover/get-paginated-events?"
                + urllib.parse.urlencode(params)
            )
            for event in parse_luma(data.get("entries", [])):
                events[event["id"]] = event
            cursor = data.get("next_cursor")
            time.sleep(REQUEST_DELAY)
            if not data.get("has_more") or not cursor:
                break

    # Luma complète parfois avec des villes lointaines : on ne garde que la France.
    return [event for event in events.values() if event["country"] == "France" or event["online"]]


# ---------------------------------------------------------------- académique


def _load_yaml(text):
    # Importé ici : sans PyYAML, seules les deux sources académiques tombent.
    import yaml

    return yaml.safe_load(text) or []


def parse_sec_deadlines(items, today=None):
    today = today or date.today()
    events = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tags = item.get("tags") or []
        if "CONF" not in tags:
            continue
        year = item.get("year")
        start, end = parse_date_range(str(item.get("date") or ""), int(year) if year else None)
        if not start:
            continue

        deadlines = item.get("deadline") or []
        if isinstance(deadlines, str):
            deadlines = [deadlines]
        next_deadline = min(
            (d for d in (parse_iso_date(str(value)) for value in deadlines) if d and d >= today),
            default=None,
        )

        place = str(item.get("place") or "")
        pieces = [piece.strip() for piece in place.split(",")]
        events.append(
            make_event(
                source="sec-deadlines",
                native_id=f"{item.get('name')}{year}",
                title=f"{item.get('name')} {year} — {item.get('description', '')}".strip(" —"),
                url=item.get("link", ""),
                kind="academic",
                start=start,
                end=end,
                city=pieces[0] if pieces else "",
                country=pieces[-1] if len(pieces) > 1 else "",
                online="online" in place.lower() or "virtual" in place.lower(),
                # Le dépôt liste aussi du génie logiciel (ICSE, ISSTA…) sans tag SEC.
                topics=(
                    ["security"] if {"SEC", "PRIV", "CRYPTO"} & set(tags) else ["software"]
                ) + [tag.lower() for tag in tags if tag in {"PRIV", "CRYPTO"}],
                flagship="TOP4" in tags,
                rank="A*" if "ASTAR" in tags else None,
                cfp_end=next_deadline.isoformat() if next_deadline else None,
            )
        )
    return events


def parse_ai_deadlines(items, today=None):
    today = today or date.today()
    events = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start = parse_iso_date(item.get("start"))
        if not start:
            start, _ = parse_date_range(str(item.get("date") or ""), item.get("year"))
        if not start:
            continue
        end = parse_iso_date(item.get("end")) or start

        next_deadline = None
        for deadline in item.get("deadlines") or []:
            if not isinstance(deadline, dict):
                continue
            if deadline.get("type") not in {"submission", "paper", "abstract"}:
                continue
            value = parse_iso_date(str(deadline.get("date") or ""))
            if value and value >= today and (next_deadline is None or value < next_deadline):
                next_deadline = value

        title = f"{item.get('title')} {item.get('year')}"
        if item.get("full_name"):
            title += f" — {item['full_name']}"

        events.append(
            make_event(
                source="ai-deadlines",
                native_id=item.get("id") or title,
                title=title,
                url=item.get("link", ""),
                kind="academic",
                start=start,
                end=end,
                city=item.get("city", ""),
                country=item.get("country", ""),
                topics=list(item.get("tags") or []),
                hindex=item.get("hindex") or 0,
                flagship=(item.get("hindex") or 0) >= 100,
                cfp_end=next_deadline.isoformat() if next_deadline else None,
            )
        )
    return events


def fetch_sec_deadlines():
    text = http_get(
        "https://raw.githubusercontent.com/sec-deadlines/"
        "sec-deadlines.github.io/master/_data/conferences.yml",
        accept="text/plain",
    )
    return parse_sec_deadlines(_load_yaml(text))


def fetch_ai_deadlines():
    events = []
    for name in AI_DEADLINES_FILES:
        text = http_get(
            "https://raw.githubusercontent.com/huggingface/ai-deadlines/"
            f"main/src/data/conferences/{name}.yml",
            accept="text/plain",
        )
        events += parse_ai_deadlines(_load_yaml(text))
        time.sleep(REQUEST_DELAY / 5)
    return events


# ---------------------------------------------------------------- orchestration


SOURCES = [
    ("confs.tech", fetch_confs_tech),
    ("dev.events", fetch_dev_events),
    ("sec-deadlines", fetch_sec_deadlines),
    ("ai-deadlines", fetch_ai_deadlines),
    ("Devpost", fetch_devpost),
    ("MLH", fetch_mlh),
    ("CTFtime", fetch_ctftime),
    ("Luma", fetch_luma),
]


def is_upcoming(event, today=None, horizon_days=HORIZON_DAYS):
    """
    À venir = pas encore commencé. Exception : un hackathon déjà ouvert
    reste rejoignable tant que sa période de soumission dure encore quelques jours.
    """
    today = today or date.today()
    start = date.fromisoformat(event["start"])
    end = date.fromisoformat(event["end"])

    if start > today + timedelta(days=horizon_days):
        return False
    if start >= today:
        return True
    return event["kind"] == "hackathon" and end >= today + timedelta(days=7)


HACKATHON_TITLE = re.compile(r"\b(hackathons?|hack ?days?|game ?jam)\b", re.IGNORECASE)
CTF_TITLE = re.compile(r"\b(ctf|capture the flag)\b", re.IGNORECASE)


def refine_kind(event):
    # Luma et dev.events ne typent pas leurs événements : le titre le dit souvent.
    if event["kind"] in {"meetup", "conference"}:
        if HACKATHON_TITLE.search(event["title"]):
            event["kind"] = "hackathon"
        elif CTF_TITLE.search(event["title"]):
            event["kind"] = "ctf"
    return event


def fetch_all(sources=SOURCES, today=None):
    events = []
    failures = []

    for name, fetch in sources:
        try:
            found = fetch()
        except Exception as error:
            failures.append(name)
            print(f"⚠️ {name} indisponible : {error}", flush=True)
            continue
        print(f"📥 {name} : {len(found)} événements", flush=True)
        events += found

    if failures and len(failures) == len(sources):
        raise RuntimeError("Toutes les sources ont échoué")

    return [refine_kind(event) for event in events if is_upcoming(event, today)], failures
