# 📡 Community Radar

Community Radar is a free bot that watches upcoming conferences, hackathons, CTFs and local tech meetups. It scores them against a personal profile and posts the most relevant new events to Discord once a day.

It is built like [Research Radar](https://github.com/thisisonly4test/Research-Radar): plain Python, deterministic scoring, GitHub Actions, a Discord webhook and no LLM or paid API.

## Profile

- **Topics:** cybersecurity and robotics first, then general tech (AI, dev, cloud, data, open source, hardware).
- **Location rule:**
  - Major conferences (academic flagships, well-known industry conferences) are kept wherever they happen in the world.
  - Small formats (hackathons, meetups, on-site CTFs) favour France first, then online. Small events abroad are pushed out.
- **Cadence:** every day, only events never sent before, at most 8 per day.

## Pipeline

```text
8 sources (see below)
  ↓
normalisation: title, dates, city/country, online, kind, topics
  ↓
upcoming filter (not started yet, within 365 days; open hackathons stay joinable)
  ↓
kind refinement from the title (a Luma "Hackathon @42Paris" becomes a hackathon)
  ↓
cross-source deduplication (normalised title + start date)
  ↓
already-sent filter (data/seen.json)
  ↓
transparent relevance scoring
  ├─ topic fit (cyber / robotics / tech)
  ├─ notability (flagship, A*, CTFtime weight, prize, MLH, well-known names)
  ├─ location rule by kind
  ├─ timing (start date, open call for papers)
  └─ penalties (business events, paid masterclasses, generic "International Conference on…")
  ↓
score ≥ 50
  ↓
diversity selection (soft penalty per kind and per topic), top 8
  ↓
Discord (embeds sorted by date)
  ↓
mark sent events as seen
```

## Sources

| Source | What it brings | Access |
| --- | --- | --- |
| [confs.tech](https://confs.tech) | Tech conferences worldwide (security, IoT, data, devops, open source…) | Public JSON on GitHub |
| [dev.events](https://dev.events) | Newly listed developer conferences | RSS |
| [sec-deadlines](https://sec-deadlines.github.io) | Academic security conferences (S&P, CCS, USENIX Security, NDSS…) | YAML on GitHub |
| [ai-deadlines](https://huggingface.co/spaces/huggingface/ai-deadlines) | Robotics and AI flagships (ICRA, IROS, CoRL, RSS, NeurIPS, ICML, ICLR, CVPR) | YAML on GitHub |
| [Devpost](https://devpost.com/hackathons) | Upcoming and open hackathons, mostly online | Public JSON API |
| [MLH](https://www.mlh.com) | Official student hackathons | JSON embedded in the season page |
| [CTFtime](https://ctftime.org) | CTF competitions over the next 120 days, with team-voted weight | Public JSON API |
| [Luma](https://lu.ma) | Local events around 10 French tech cities | Public discovery endpoint |

Every source is optional. If one fails, the digest still goes out and lists the missing sources. If all of them fail, the run fails and Discord receives an alert.

Luma's endpoint is not a documented API and may change. Meetup and Eventbrite are not used because they need OAuth or a paid token.

## Scoring

The score measures how well an event fits this radar. It is not a judgement of the event's quality.

| Signal | Points |
| --- | --- |
| Cybersecurity or robotics topic | +30 each |
| Tech only | +18 |
| Several topics | +5 |
| Academic flagship / A* | +30 / +20 |
| Industry conference | +10 |
| Well-known event (DEF CON, BSides, SSTIC, Hexacon, FOSDEM, ROSCon, VivaTech…) | +15 |
| Hackathon prize ≥ $10k / ≥ $1k, ≥ 500 registrations, MLH | +10 / +5, +5, +8 |
| CTFtime weight | up to +15 (unknown weight −10) |
| France | +30 for small formats, +25 for conferences |
| Online | +8 for hackathons/meetups, +3 for conferences, 0 for CTFs |
| Europe / rest of the world | +5 / 0 for conferences, −30 / −45 for small formats |
| Starts in 2–90 days / 91–200 days | +10 / +5 |
| Call for papers closing within 60 days | +8 |
| Business, networking, paid masterclass, generic conference title | −15 to −30 |
| Local meetup | −10, then +8 for a project format, +5 for a developer audience |

Labels: 🟢 ≥ 60 "À NE PAS RATER", 🟡 ≥ 40 "À SURVEILLER". Only events scoring at least `MIN_SCORE` (50) are sent.

All patterns and weights live in `scoring.py`.

## Memory

`data/seen.json` maps each sent event to its end date, both by source ID and by `title|start` key. The second key stops the same event from coming back through another source. Entries are dropped once the event is over.

Only sent events are stored. A relevant event that missed today's quota stays in the queue and can be sent on a later day.

## Discord message

Each event is an embed in French: title and link, dates with countdown, place (🇫🇷 / 🌐), call-for-papers deadline, prize, organiser, kind, priority, topics and reasons. Messages are split to respect Discord's limits (10 embeds, 6000 characters).

## Setup

1. Push this folder to a GitHub repository.
2. Create a Discord webhook (channel settings → Integrations → Webhooks).
3. Add it as the `DISCORD_WEBHOOK` repository secret.
4. Run the workflow manually once with `dry_run` checked to read the digest in the logs, then once without it.

The workflow runs every day at 07:00 UTC.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=true python -u main.py          # prints the Discord payloads, does not touch seen.json
DISCORD_WEBHOOK=https://... python -u main.py
```

## Testing

```bash
python -m unittest discover -s tests -v
```

The tests use only the standard library and cover date parsing, every source parser, the location rule, penalties, deduplication, diversity, Discord payload limits, seen-state persistence and the main pipeline.

## Files

- `collectors.py`: the 8 sources, date parsing, upcoming filter, kind refinement.
- `scoring.py`: topics, location rule, scoring, deduplication, diversity selection.
- `main.py`: orchestration, Discord formatting and delivery, seen-state.
- `tests/`: regression tests.
- `data/seen.json`: events already sent.
