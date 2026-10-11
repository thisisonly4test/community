"""
Score de pertinence des événements, déterministe et explicable.

Le score mesure l'adéquation au radar (thèmes + règles de lieu), pas la
qualité de l'événement. Règle de lieu demandée :
  - grandes conférences : le monde entier convient ;
  - petits formats (hackathon, meetup, CTF sur place) : la France d'abord,
    l'en ligne ensuite, l'étranger presque jamais.
"""

import re
import unicodedata
from datetime import date

CYBER = "Cybersécurité"
ROBOTICS = "Robotique"
TECH = "Tech"

DOMAIN_PATTERNS = {
    CYBER: re.compile(
        r"\b(cyber\w*|infosec|hacking|pentest\w*|"
        r"ctf|capture the flag|malware|forensics?|osint|cryptograph\w*|"
        r"red ?team\w*|blue ?team\w*|bug ?bounty|vulnerabilit\w*|"
        r"zero ?trust|appsec|devsecops|owasp|reverse engineering|"
        r"ransomware|bsides|def ?con|black ?hat|sstic|hexacon|grehack|le hack|"
        r"incyber|fic)\b",
        re.IGNORECASE,
    ),
    ROBOTICS: re.compile(
        r"\b(robot\w*|robotique|ros2|roscon|drones?|uavs?|humano[iï]d\w*|"
        r"embodied|m[ée]catroni\w*|mechatronic\w*|autonomous (systems?|vehicles?|driving)|"
        r"cobots?|slam|icra|iros|corl|automatica)\b",
        re.IGNORECASE,
    ),
    TECH: re.compile(
        r"\b(ai|ia|llms?|genai|gen ai|machine learning|deep learning|"
        r"intelligence artificielle|artificial intelligence|neural|agents?|agentic|"
        r"data|cloud|devops|sre|kubernetes|k8s|docker|open ?source|linux|python|"
        r"rust|golang|javascript|typescript|java|web|apis?|developers?|d[ée]veloppeu\w*|"
        r"devs?|software|logiciels?|programm\w*|code|coding|hackathons?|iot|embedded|"
        r"embarqu[ée]\w*|hardware|fpga|quantum|quantique|computer vision|nlp|"
        r"blockchain|tech|machine-learning|computer-vision)\b",
        re.IGNORECASE,
    ),
}

# "security" seul est ambigu ("food security") : il ne compte qu'avec un signal tech.
CYBER_WEAK = re.compile(
    r"\b(security|s[ée]curit[ée]|privacy|threats?|exploits?)\b",
    re.IGNORECASE,
)

# Événements reconnus : un bonus de notoriété, quel que soit le lieu.
NOTABLE = re.compile(
    r"\b(def ?con|black ?hat|bsides|forum incyber|sstic|hexacon|rsa ?conference|"
    r"chaos communication|offensivecon|nullcon|hack\.lu|insomni.?hack|le hack|"
    r"nuit du hack|barbhack|pass the salt|grehack|european cyber week|"
    r"vivatech|viva ?technology|kubecon|fosdem|devoxx|roscon|automatica|"
    r"european robotics forum|innorobo|web summit|ai ?paris|ai summit|"
    r"dotai|dotjs|mixit|breizhcamp|devfest|pycon|rustconf|euro ?rust)\b",
    re.IGNORECASE,
)

# Bruit fréquent sur Luma : business, afterworks, bien-être.
NOISE = re.compile(
    r"\b(sales|vente|finance|fintech|juridique|legal|rh|recrutement|recruiting|"
    r"marketing|immobilier|real estate|afterwork|ap[ée]ro|drinks|networking event|"
    r"founders?|investisseurs?|investors?|vc|lev[ée]e de fonds|podcast|yoga|"
    r"breathwork|wine|vins?|d[îi]ner|brunch|career|carri[èe]re|job ?fair|"
    r"business|entrepreneu\w*|leadership|management|coaching|bien-[êe]tre|"
    r"networking|pitch|startups?|gala|cocktail|masterclass|padel)\b",
    re.IGNORECASE,
)

# Conférences « attrape-tout » à intitulé générique, souvent prédatrices.
GENERIC_CONFERENCE = re.compile(
    r"^(\d+(st|nd|rd|th) )?(annual )?(global|world|international) "
    r"(summit|conference|congress|symposium) on\b",
    re.IGNORECASE,
)

# Petits formats où l'on construit ou présente des projets.
PROJECT_FORMAT = re.compile(
    r"\b(hackathons?|game ?jam|demo ?day|build ?club|showcase|conf[ée]rences?|summit|"
    r"workshop|atelier|talks?|lightning)\b",
    re.IGNORECASE,
)

# Signes qu'un meetup s'adresse à des gens qui construisent, pas à des décideurs.
BUILDER_SIGNAL = re.compile(
    r"\b(d[ée]velop\w*|devs?|code|coding|open ?source|rex|demo|build\w*|"
    r"prod|production|architecture|engineering|ing[ée]nie\w*|python|rust|java|"
    r"linux|kubernetes|github|hands-on|maker\w*|fablab)\b",
    re.IGNORECASE,
)

# Formations payantes vendues comme événements sur dev.events.
PAID_FORMATS = {"masterclass", "workshop", "webinar"}

FRENCH_CITIES = {
    "paris", "lyon", "marseille", "toulouse", "lille", "bordeaux", "nantes",
    "rennes", "grenoble", "montpellier", "strasbourg", "nice", "sophia antipolis",
    "saclay", "palaiseau", "brest", "angers", "tours", "dijon", "rouen", "caen",
    "nancy", "metz", "clermont-ferrand", "orléans", "orleans", "la défense",
}

EUROPE = {
    "france", "germany", "allemagne", "spain", "espagne", "italy", "italie",
    "netherlands", "the netherlands", "pays-bas", "belgium", "belgique",
    "switzerland", "suisse", "austria", "autriche", "portugal", "ireland",
    "united kingdom", "uk", "england", "scotland", "poland", "sweden", "denmark",
    "norway", "finland", "czech republic", "czechia", "romania", "greece",
    "hungary", "slovakia", "slovenia", "croatia", "luxembourg", "estonia",
    "latvia", "lithuania", "bulgaria", "serbia", "malta", "cyprus", "iceland",
    "de", "es", "it", "nl", "be", "ch", "at", "pt", "ie", "gb", "pl", "se",
    "dk", "no", "fi", "cz", "ro", "gr", "hu", "sk", "si", "hr", "lu",
}

SMALL_KINDS = {"hackathon", "meetup", "ctf"}

KIND_LABELS = {
    "academic": "🎓 Conférence scientifique",
    "conference": "🎤 Conférence",
    "hackathon": "🏆 Hackathon",
    "ctf": "🚩 CTF",
    "meetup": "🤝 Meetup",
}

SOURCE_PRIORITY = ["sec-deadlines", "ai-deadlines", "confs.tech", "dev.events", "MLH", "Devpost", "CTFtime", "Luma"]


def _text(event):
    return " ".join(
        [event.get("title", ""), " ".join(event.get("topics", [])), event.get("organizer", "") or ""]
    )


def detect_domains(event):
    text = _text(event)
    domains = [domain for domain, pattern in DOMAIN_PATTERNS.items() if pattern.search(text)]

    if CYBER not in domains and CYBER_WEAK.search(text) and TECH in domains:
        domains.insert(0, CYBER)
    # Les sources thématiques (confs.tech/security, sec-deadlines) disent déjà le thème.
    if CYBER not in domains and "security" in event.get("topics", []):
        domains.insert(0, CYBER)

    # Le format suffit à dire le thème : un CTF est de la sécu, un hackathon de la tech.
    if event["kind"] == "ctf" and CYBER not in domains:
        domains.insert(0, CYBER)
    if event["kind"] == "hackathon" and not domains:
        domains.append(TECH)

    return domains


def location_class(event):
    """france | europe | world | online | unknown"""
    country = (event.get("country") or "").strip().lower()
    city = (event.get("city") or "").strip().lower()

    if country in {"france", "fr"} or city in FRENCH_CITIES or "france" in city:
        return "france"
    if event.get("online") and not country and not city:
        return "online"
    if country in EUROPE:
        return "europe"
    if country or city:
        return "world"
    return "online" if event.get("online") else "unknown"


def days_until(event, today):
    return (date.fromisoformat(event["start"]) - today).days


def score_event(event, today=None):
    today = today or date.today()
    reasons = []
    score = 0

    domains = detect_domains(event)
    event["domains"] = domains
    event["primary_domain"] = domains[0] if domains else "Autre"

    if not domains:
        event["score"] = 0
        event["reasons"] = ["hors thématique"]
        return event

    # 1. Thème. La cybersécurité et la robotique sont le cœur du radar.
    if CYBER in domains:
        score += 30
        reasons.append("cybersécurité")
    if ROBOTICS in domains:
        score += 30
        reasons.append("robotique")
    if domains == [TECH]:
        score += 18
        reasons.append("tech")
    if len(domains) >= 2:
        score += 5

    kind = event["kind"]
    title = event.get("title", "")

    # 2. Notoriété / sérieux de l'événement.
    if kind == "academic":
        if event.get("flagship"):
            score += 30
            reasons.append("conférence de référence")
        elif event.get("rank") == "A*":
            score += 20
            reasons.append("classée A*")
    elif kind == "conference":
        score += 10
        if (event.get("format") or "").lower() in PAID_FORMATS:
            score -= 20
            reasons.append("formation payante")
    elif kind == "hackathon":
        prize = event.get("prize_value") or 0
        if prize >= 10000:
            score += 10
            reasons.append(f"dotation {event.get('prize')}")
        elif prize >= 1000:
            score += 5
        if (event.get("registrations") or 0) >= 500:
            score += 5
            reasons.append(f"{event['registrations']} inscrits")
        if event["source"] == "MLH":
            score += 8
            reasons.append("hackathon officiel MLH")
    elif kind == "meetup":
        # Luma mélange tout : un meetup tech générique ne passe pas seul.
        score -= 10
        if PROJECT_FORMAT.search(title):
            score += 8
            reasons.append("format projet")
        if BUILDER_SIGNAL.search(title):
            score += 5
            reasons.append("pour développeurs")
    elif kind == "ctf":
        weight = event.get("weight") or 0
        if weight > 0:
            score += min(15, round(weight / 4))
            reasons.append(f"poids CTFtime {weight:g}")
        else:
            score -= 10
        if (event.get("restrictions") or "Open") != "Open":
            score -= 10
            reasons.append(f"réservé ({event['restrictions']})")

    if NOTABLE.search(title):
        score += 15
        reasons.append("événement reconnu")

    if GENERIC_CONFERENCE.search(title):
        score -= 15
        reasons.append("intitulé générique")

    noise = NOISE.findall(title)
    if noise:
        score -= min(30, 15 * len(noise))
        reasons.append("orienté business")

    # 3. Lieu, selon le format.
    place = location_class(event)
    event["location_class"] = place
    small = kind in SMALL_KINDS
    if place == "france":
        score += 30 if small else 25
        reasons.append("en France")
    elif place == "online":
        # Un CTF en ligne est la norme : pas de bonus.
        score += 0 if kind == "ctf" else 8 if small else 3
        reasons.append("en ligne")
    elif place == "europe":
        score += -30 if small else 5
    else:
        score += -45 if small else 0

    # 4. Délai : assez tôt pour s'organiser, pas si loin que les dates bougent.
    days = days_until(event, today)
    if days < 2:
        score -= 5
    elif days <= 90:
        score += 10
    elif days <= 200:
        score += 5

    cfp_end = event.get("cfp_end")
    if cfp_end:
        cfp_days = (date.fromisoformat(cfp_end) - today).days
        if 0 <= cfp_days <= 60:
            score += 8
            reasons.append(f"appel à contributions ouvert ({cfp_days} j)")

    event["score"] = max(0, min(100, score))
    event["reasons"] = reasons
    event["priority"] = priority_label(event["score"])
    return event


def priority_label(score):
    if score >= 60:
        return "🟢 À NE PAS RATER"
    if score >= 40:
        return "🟡 À SURVEILLER"
    return "⚪ FAIBLE PRIORITÉ"


def rank_events(events, today=None):
    scored = [score_event(event, today) for event in events]
    return sorted(scored, key=lambda event: (-event["score"], event["start"]))


# ---------------------------------------------------------------- doublons


def normalize_title(title):
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    title = title.lower()
    title = re.sub(r"\b(19|20)\d{2}\b", " ", title)
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return " ".join(title.split())


def dedupe_key(event):
    return f"{normalize_title(event['title'])}|{event['start']}"


def dedupe(events):
    """
    Un même événement peut venir de plusieurs sources (confs.tech + dev.events).
    On garde la version de la source la plus riche et on note les autres.
    """
    def priority(event):
        source = event["source"]
        return SOURCE_PRIORITY.index(source) if source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)

    best = {}
    for event in sorted(events, key=priority):
        key = dedupe_key(event)
        if key in best:
            other = best[key]
            if event["source"] not in other["sources"]:
                other["sources"].append(event["source"])
            continue
        event["sources"] = [event["source"]]
        best[key] = event

    return list(best.values())


# ---------------------------------------------------------------- sélection


def select_diverse(ranked, limit, kind_penalty=10, domain_penalty=5):
    """
    Pénalité douce : un 4e CTF doit vraiment dépasser un hackathon en France
    pour passer. Un très bon événement peut toujours gagner.
    """
    remaining = list(ranked)
    selected = []
    kind_counts = {}
    domain_counts = {}

    while remaining and len(selected) < limit:
        def adjusted(event):
            return (
                event["score"]
                - kind_penalty * kind_counts.get(event["kind"], 0)
                - domain_penalty * domain_counts.get(event["primary_domain"], 0)
            )

        best = max(remaining, key=lambda event: (adjusted(event), -days_until_iso(event)))
        remaining.remove(best)
        best["selection_score"] = adjusted(best)
        selected.append(best)
        kind_counts[best["kind"]] = kind_counts.get(best["kind"], 0) + 1
        domain_counts[best["primary_domain"]] = domain_counts.get(best["primary_domain"], 0) + 1

    return selected


def days_until_iso(event):
    return date.fromisoformat(event["start"]).toordinal()
