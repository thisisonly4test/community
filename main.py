import json
import os
import urllib.request
from datetime import date

from collectors import fetch_all
from scoring import KIND_LABELS, dedupe, dedupe_key, rank_events, select_diverse

SEEN_FILE = "data/seen.json"
MAX_NEW_EVENTS = 8
# Même seuil que « 🟡 À SURVEILLER » relevé de 10 : sous 50, le radar se
# remplit de meetups génériques et de conférences mineures.
MIN_SCORE = 50

# Limites Discord par message.
DISCORD_MAX_EMBEDS = 10
DISCORD_MAX_CHARS = 5800

WEEKDAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MONTHS = [
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
]


# ---------------------------------------------------------------- mémoire


def load_seen():
    """
    { identifiant : date de fin }. Les doublons inter-sources sont aussi
    mémorisés par clé titre|date, pour ne pas renvoyer un événement déjà
    reçu via une autre source.
    """
    if not os.path.exists(SEEN_FILE):
        return {}

    with open(SEEN_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"{SEEN_FILE} doit contenir un objet JSON.")

    return data


def prune_seen(seen, today=None):
    # Un événement terminé ne peut plus revenir : inutile de le garder.
    today = (today or date.today()).isoformat()
    return {key: end for key, end in seen.items() if end >= today}


def save_seen(seen, today=None):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)

    with open(SEEN_FILE, "w", encoding="utf-8") as file:
        json.dump(dict(sorted(prune_seen(seen, today).items())), file, indent=2, ensure_ascii=False)


def is_seen(event, seen):
    return event["id"] in seen or dedupe_key(event) in seen


def mark_seen(seen, events):
    # Seuls les événements envoyés sont marqués : un bon événement non retenu
    # aujourd'hui (quota atteint) pourra partir un autre jour.
    for event in events:
        seen[event["id"]] = event["end"]
        seen[dedupe_key(event)] = event["end"]

    save_seen(seen)

    print("💾 Mémoire mise à jour", flush=True)


# ---------------------------------------------------------------- mise en forme


# Une couleur par format : l'œil repère le type avant de lire.
KIND_COLORS = {
    "academic": 0x9B59B6,
    "conference": 0x3498DB,
    "hackathon": 0xE67E22,
    "ctf": 0xE74C3C,
    "meetup": 0x2ECC71,
}

# Sections du digest, dans l'ordre d'affichage.
SECTIONS = [
    ("france", "🇫🇷 En France"),
    ("major", "🌍 Grandes conférences"),
    ("online", "🌐 En ligne"),
]

# Raisons déjà visibles ailleurs dans la carte (lieu, thème) : inutile de les répéter.
REDUNDANT_REASONS = {"en France", "en ligne", "tech", "cybersécurité", "robotique"}


def format_day(value, today=None, weekday=True):
    today = today or date.today()
    day = date.fromisoformat(value)
    text = f"{day.day} {MONTHS[day.month - 1]}"
    if weekday:
        text = f"{WEEKDAYS[day.weekday()]} {text}"
    if day.year != today.year:
        text += f" {day.year}"
    return text


def format_countdown(event, today=None):
    today = today or date.today()
    days = (date.fromisoformat(event["start"]) - today).days
    if days > 1:
        return f"dans {days} j"
    if days == 1:
        return "demain"
    if days == 0:
        return "aujourd'hui"
    return "déjà ouvert"


def format_dates(event, today=None):
    text = f"**{format_day(event['start'], today)}**"
    if event["end"] != event["start"]:
        text += f" → {format_day(event['end'], today)}"
    return f"{text}  ·  *{format_countdown(event, today)}*"


def format_place(event):
    city = event.get("city") or ""
    country = event.get("country") or ""
    if event.get("location_class") == "france":
        country = "France"

    if city and country and city != country:
        place = f"**{city}**, {country}"
    else:
        place = f"**{city or country}**" if city or country else ""

    if event.get("location_class") == "france":
        place += " 🇫🇷"

    if place and event.get("online"):
        return f"📍 {place}  ·  🌐 aussi en ligne"
    if place:
        return f"📍 {place}"
    if event.get("online"):
        return "🌐 **En ligne**"
    return "📍 Lieu non précisé"


def build_extras(event, today=None):
    today = today or date.today()
    extras = []

    if event.get("cfp_end") and event["cfp_end"] >= today.isoformat():
        extras.append(f"📝 Appel à contributions avant le **{format_day(event['cfp_end'], today, weekday=False)}**")
    if event.get("prize"):
        extras.append(f"💰 {event['prize']}")
    if event.get("ctf_format"):
        extras.append(f"🧩 {event['ctf_format']}")

    return extras


def section_of(event):
    if event.get("location_class") == "france":
        return "france"
    if event["kind"] in {"academic", "conference"} and event.get("location_class") != "online":
        return "major"
    return "online"


def build_embed(event, today=None):
    kind = KIND_LABELS.get(event["kind"], event["kind"])
    domains = " · ".join(event.get("domains", []))
    author = kind.upper() if not domains else f"{kind.upper()}  ·  {domains}"

    lines = [f"📅 {format_dates(event, today)}", format_place(event)]
    lines += build_extras(event, today)

    reasons = [reason for reason in event.get("reasons", []) if reason not in REDUNDANT_REASONS]
    # Le délai de CFP est déjà affiché en clair au-dessus.
    reasons = [reason for reason in reasons if not reason.startswith("appel à contributions")]
    if reasons:
        lines.append(f"> {' · '.join(reasons)}")

    # "🟢 À NE PAS RATER" devient "🟢 À ne pas rater" : moins criard en pied de carte.
    emoji, _, label = event.get("priority", "⚪ FAIBLE PRIORITÉ").partition(" ")
    priority = f"{emoji} {label.capitalize()}"
    sources = ", ".join(event.get("sources", [event["source"]]))

    return {
        "author": {"name": author[:256]},
        "title": event["title"][:256],
        "url": event["url"],
        "description": "\n".join(lines)[:1500],
        "color": KIND_COLORS.get(event["kind"], 0x99AAB5),
        "footer": {"text": f"{priority}  ·  score {event.get('score', 0)}  ·  {sources}"},
    }


def embed_size(embed):
    return (
        len(embed["author"]["name"])
        + len(embed["title"])
        + len(embed["description"])
        + len(embed["footer"]["text"])
    )


def build_payloads(events, today=None):
    """
    Un message par section (France, grandes conférences, en ligne), chacun
    avec son titre : le digest se lit par blocs au lieu d'une pile de cartes.
    """
    grouped = {key: [] for key, _ in SECTIONS}
    for event in events:
        grouped[section_of(event)].append(event)

    summary = "  ·  ".join(
        f"{title.split()[0]} {len(grouped[key])}" for key, title in SECTIONS if grouped[key]
    )
    header = f"## 📡 Community Radar\n**{len(events)} nouveaux événements**  —  {summary}"

    payloads = []
    first = True
    for key, title in SECTIONS:
        section = sorted(grouped[key], key=lambda event: event["start"])
        if not section:
            continue

        content = f"### {title}"
        if first:
            content = f"{header}\n\n{content}"
            first = False

        current = {"content": content, "embeds": []}
        used = len(content)
        for event in section:
            embed = build_embed(event, today)
            size = embed_size(embed)
            if current["embeds"] and (
                len(current["embeds"]) >= DISCORD_MAX_EMBEDS or used + size > DISCORD_MAX_CHARS
            ):
                payloads.append(current)
                current = {"embeds": []}
                used = 0
            current["embeds"].append(embed)
            used += size
        payloads.append(current)

    return payloads


# ---------------------------------------------------------------- Discord


def dry_run():
    return os.environ.get("DRY_RUN") == "true"


def post_discord(payload):
    if dry_run():
        print(json.dumps(payload, indent=2, ensure_ascii=False), flush=True)
        return

    webhook = os.environ.get("DISCORD_WEBHOOK")

    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK secret is missing")

    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Community-Radar/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Discord returned HTTP {response.status}")


def send_discord(events, failures=()):
    payloads = build_payloads(events)

    # Une source tombée ne bloque pas l'envoi, mais on le signale.
    if failures:
        payloads[-1]["content"] = (
            payloads[-1].get("content", "") + f"\n⚠️ Sources indisponibles : {', '.join(failures)}"
        ).strip()

    for payload in payloads:
        post_discord(payload)


# ---------------------------------------------------------------- pipeline


def main():
    events, failures = fetch_all()
    events = dedupe(events)

    print(f"📚 {len(events)} événements à venir (après dédoublonnage)", flush=True)

    seen = load_seen()
    new_events = [event for event in events if not is_seen(event, seen)]

    print(f"🆕 {len(new_events)} jamais envoyés", flush=True)

    ranked = rank_events(new_events)

    print("\n🏆 TOP SCORE BRUT :", flush=True)
    for event in ranked[:15]:
        print(
            f"{event['score']:>3} | {event['kind']:<10} | "
            f"{event.get('location_class', ''):<7} | {event['start']} | {event['title'][:70]}",
            flush=True,
        )

    relevant = [event for event in ranked if event["score"] >= MIN_SCORE]

    if not relevant:
        print(f"🔕 Aucun nouvel événement au-dessus de {MIN_SCORE} : rien d'envoyé.", flush=True)
        save_seen(seen)
        return

    selected = select_diverse(relevant, limit=MAX_NEW_EVENTS)

    print(f"\n📡 Envoi de {len(selected)} événements ({len(relevant)} pertinents en attente)", flush=True)
    for event in selected:
        print(f"  {event['score']:>3} | {event['start']} | {event['title'][:70]}", flush=True)

    send_discord(selected, failures)

    if dry_run():
        print("🧪 DRY_RUN : rien d'envoyé, mémoire inchangée", flush=True)
        return

    print("✅ Digest envoyé sur Discord", flush=True)

    mark_seen(seen, selected)


if __name__ == "__main__":
    main()
