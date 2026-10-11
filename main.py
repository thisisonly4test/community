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

COUNTRY_FLAGS = {"france": "🇫🇷", "fr": "🇫🇷"}


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


def format_day(value):
    day = date.fromisoformat(value)
    return f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]} {day.year}"


def format_dates(event, today=None):
    today = today or date.today()
    start = date.fromisoformat(event["start"])
    end = date.fromisoformat(event["end"])

    text = format_day(event["start"])
    if end != start:
        text += f" → {format_day(event['end'])}"

    days = (start - today).days
    if days > 1:
        text += f" (dans {days} j)"
    elif days == 1:
        text += " (demain)"
    elif days == 0:
        text += " (aujourd'hui)"
    else:
        text += " (déjà ouvert)"

    return text


def format_place(event):
    place = ", ".join(part for part in (event.get("city"), event.get("country")) if part)
    flag = COUNTRY_FLAGS.get((event.get("country") or "").lower(), "📍")
    if event.get("location_class") == "france" and flag == "📍":
        flag = "🇫🇷"

    if place and event.get("online"):
        return f"{flag} {place} + 🌐 en ligne"
    if place:
        return f"{flag} {place}"
    if event.get("online"):
        return "🌐 En ligne"
    return "📍 Lieu non précisé"


def build_extras(event, today=None):
    today = today or date.today()
    lines = []

    if event.get("cfp_end"):
        days = (date.fromisoformat(event["cfp_end"]) - today).days
        if days >= 0:
            lines.append(f"📝 Appel à contributions jusqu'au {format_day(event['cfp_end'])}")
    if event.get("prize"):
        lines.append(f"💰 Dotation : {event['prize']}")
    if event.get("organizer"):
        lines.append(f"🏢 {event['organizer']}")
    if event.get("ctf_format"):
        lines.append(f"🧩 Format : {event['ctf_format']}")

    return lines


def build_embed(event, today=None):
    score = event.get("score", 0)

    description_lines = [f"📅 {format_dates(event, today)}", format_place(event)]
    description_lines += build_extras(event, today)

    themes = " · ".join(event.get("domains", [])) or "Autre"
    topics = [topic for topic in event.get("topics", []) if topic and topic.lower() not in {"conference", "ctf"}]
    if topics:
        themes += "\n" + ", ".join(topics[:5])

    return {
        "title": event["title"][:256],
        "url": event["url"],
        "description": "\n".join(description_lines)[:1500],
        "color": 0x57F287 if score >= 60 else 0xFEE75C if score >= 40 else 0x99AAB5,
        "fields": [
            {
                "name": "Type",
                "value": KIND_LABELS.get(event["kind"], event["kind"]),
                "inline": True,
            },
            {
                "name": "Pertinence",
                "value": f"{event.get('priority', '⚪ FAIBLE PRIORITÉ')}\nScore : **{score}**",
                "inline": True,
            },
            {
                "name": "Thématiques",
                "value": themes[:1024],
                "inline": True,
            },
            {
                "name": "Pourquoi",
                "value": (" • ".join(event.get("reasons", [])) or "—")[:1024],
                "inline": False,
            },
        ],
        "footer": {"text": "Source : " + ", ".join(event.get("sources", [event["source"]]))},
    }


def embed_size(embed):
    size = len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"])
    return size + sum(len(field["name"]) + len(field["value"]) for field in embed["fields"])


def build_payloads(events, today=None):
    header = (
        "📡 **COMMUNITY RADAR**\n"
        f"**{len(events)} nouvel(le)s événement(s) à venir** — "
        "conférences, hackathons, CTF, meetups.\n"
        "_Score = adéquation au radar (thème, lieu, notoriété), pas qualité de l'événement._"
    )

    payloads = []
    current = {"content": header, "embeds": []}
    used = len(header)

    for event in events:
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

    if current["embeds"]:
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
    # Lecture plus naturelle dans Discord : du plus proche au plus lointain.
    selected.sort(key=lambda event: event["start"])

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
