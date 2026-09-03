# Hardware internships watcher (Summer 2027)

A 24/7 scraper that polls hundreds of company career sites and pushes every **new hardware
internship posting** to your phone the moment it appears, so you can apply first.

* **Direct polling of ~480 boards** on Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Oracle HCM,
  plus Tesla, Amazon and Microsoft's own career APIs: NVIDIA, Apple-adjacent suppliers, Qualcomm,
  Broadcom, AMD, Intel, Micron, TI, Analog Devices, SpaceX, Anduril, Blue Origin, Rocket Lab, Zoox,
  Skydio, Figure, Neuralink, Etched, Cerebras, Groq, d-Matrix, RTX, Northrop, Boeing, GM, Rivian, Bosch…
  (see `companies.yaml`).
* **Community feeds** (SimplifyJobs and vanshb03 "Summer 2027 Internships" repos) for career sites
  without a public API, refreshed many times a day.
* **Auto-discovery**: every hardware posting found through the feeds registers its company's board so
  it is polled directly from then on. Coverage grows by itself.
* **Smart filtering**: internship/co-op titles only; hardware taxonomy (electrical, board/PCB, embedded &
  firmware, ASIC/FPGA/silicon, analog/RF/photonics, robotics/controls, mechanical, test/validation);
  Summer 2027 term detection (other seasons and past years dropped, unstated terms kept but flagged);
  pure-software roles excluded unless they are embedded/silicon/avionics/robotics.
* **Eligibility heads-up**: each notification flags "US citizenship / clearance / ITAR", "no visa
  sponsorship", "PhD" etc. when the posting says so. You can also hard-exclude those.
* **Channels**: Discord, Telegram, ntfy (phone push), Slack, email, generic webhook, and an
  always-on `state/NEW_JOBS.md` log.
* **Never repeats** a posting (SQLite state, cross-source URL de-duplication).

## Quick start (5 minutes)

```bash
cd hardware-internships-scraper
python3 -m venv .venv && . .venv/bin/activate   # Windows: py -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env         # fill in at least one channel (Telegram bot token is the easiest)
                             # .env is read automatically (Windows, macOS, Linux)
python -m hwintern test-notify          # every configured channel should say "ok"

python -m hwintern run --dry-run        # first pass: builds the baseline, shows what it would send
python -m hwintern run --loop           # 24/7: polls every 5 minutes
```

The first real run notifies every currently-open matching posting that is less than
`initial_max_age_days` old (45 by default) and silently remembers the rest. After that you only get
truly new openings.

### Get notified on your phone

| Channel | Setup |
|---|---|
| **Telegram** | `@BotFather` → `/newbot` → token into `TELEGRAM_BOT_TOKEN`. Open the bot, press Start. Chat id is auto-detected. |
| **Discord** | Server → Settings → Integrations → Webhooks → copy URL into `DISCORD_WEBHOOK_URL`. Turn on push for that channel. |
| **ntfy** | Install the ntfy app, subscribe to a long random topic, set `NTFY_TOPIC`. |
| Slack / email / webhook | See `.env.example`. |

## Running it 24/7

**Option A - any always-on machine (recommended, true real-time):**

```bash
docker compose up -d          # restarts on crash and reboot, state in ./state
docker compose logs -f
```
or without Docker: `deploy/hw-internships-watcher.service` (systemd, works on a Raspberry Pi or a
free-tier cloud VM).

**Option B - zero infrastructure (GitHub Actions):** `.github/workflows/hardware-internships.yml`
runs the poll every 5 minutes and keeps the seen-jobs database in the Actions cache. Add your channel
secrets under *Settings → Secrets and variables → Actions* (`TELEGRAM_BOT_TOKEN`, or `DISCORD_WEBHOOK_URL`,
`NTFY_TOPIC`, …) and enable the workflow. GitHub may delay scheduled runs
by a few minutes when runners are busy, so Option A is tighter.

## Current personal settings (already in `config.yaml`)

| Setting | Value |
|---|---|
| Term | Summer 2027 first; every other future term is kept and flagged `other-term`; Spring/Summer/Winter 2026 dropped |
| Eligibility | Anything requiring US citizenship, a green card, a clearance, or ITAR "US person" status is **dropped**. "No visa sponsorship" postings are **kept and flagged**, because an internship on CPT does not need sponsorship |
| Level | PhD-titled roles dropped; "graduate students only" wording flagged |
| Location | Everywhere (US, Canada, Europe…) |
| Focus | ⭐ priority (sent first, urgent phone push) for robotics, mechatronics, avionics, drones/UAV, aircraft, maritime/naval/ships, UUV/AUV/USV, autonomy, GNC, satellites, exoskeletons, humanoids, embedded/firmware, controls, perception/computer vision, PCB, electrical. Mechanical and manufacturing roles still included, unstarred |
| Companies | Universities/colleges excluded |

## Setting up the phone channels

**Telegram (primary):**
1. In Telegram talk to `@BotFather`, send `/newbot`, copy the token it gives you.
2. Open your new bot's chat and press **Start** (or send it any message). That is all: the watcher
   reads the chat id from that message on its first run and remembers it.
3. Put the token in `.env` as `TELEGRAM_BOT_TOKEN=` (or as the GitHub Actions secret of the same name).
4. `python -m hwintern test-notify` should deliver a test message. If it says "no chat id yet", you
   have not pressed Start on the bot; do that and retry. `TELEGRAM_CHAT_ID` can be set explicitly too.

**Discord:** create a private server, right-click the channel → *Edit channel* → *Integrations* →
*Webhooks* → *New Webhook* → copy the URL into `DISCORD_WEBHOOK_URL`.

**ntfy (zero-signup push):** install the ntfy app, subscribe to a long random topic, set `NTFY_TOPIC`.

## Tuning what you receive (`config.yaml`)

```yaml
filters:
  target_terms: ["Summer 2027"]
  accept_other_terms: true                              # keep other seasons, flagged
  reject_terms: ["Spring 2026", "Summer 2026"]
  priority_keywords: ["robot", "drone", "avionic"]      # ⭐ and sent first
  categories: [electrical, hardware, embedded, silicon, analog_rf, robotics_controls, mechanical, test_validation]
  location_include: [", [A-Z]{2}\\b|Remote"]           # US-style "City, ST" or remote only
  location_exclude: ["India|China"]
  exclude_flags: ["citizenship-required", "phd-title"]  # drop citizenship/green-card/clearance roles, PhD roles
  exclude_sponsorship: ["Does Not Offer Sponsorship", "U.S. Citizenship is Required"]
  exclude_companies: ["\\bUniversity\\b"]
  extra_keywords: ["\\bquantum\\b"]
```

Everything under `filters` maps to `hwintern/filters.py`; the keyword lists per category live there too.

## Managing boards

```bash
python -m hwintern boards                        # every board that will be polled (+ failure counts)
python -m hwintern add-board --url https://boards.greenhouse.io/spacex/jobs/123   # paste any job URL
python -m hwintern add-board --kind workday --id "nvidia.wd5.myworkdayjobs.com|nvidia|NVIDIAExternalCareerSite" --company NVIDIA
python -m hwintern check-board greenhouse anduril   # live-fetch one board, show what would match
python -m hwintern discover --apply                 # mine the community feeds for boards we don't poll yet
python -m hwintern remove-board workday "host|tenant|site"
python -m hwintern export                           # markdown table of everything matched so far
python -m hwintern stats
python -m hwintern reset --yes                      # forget everything (next run rebuilds the baseline)
```

`companies.yaml` is generated by `scripts/build_companies.py` from a curated list plus every board that
ever carried a hardware internship in the community feeds. Boards that keep failing (renamed site,
company moved ATS) show up in `python -m hwintern boards`; delete them from the file or leave them,
they cost one failed request per cycle.

## Layout

```
hwintern/
  pipeline.py     fetch all sources in parallel -> de-dup -> classify -> fetch details when ambiguous
                  -> notify -> auto-discover boards
  filters.py      internship / hardware / term / eligibility classification
  notify.py       Discord, Telegram, Slack, ntfy, email, webhook, file, stdout
  store.py        SQLite: seen jobs, discovered boards, ETag cache
  sources/        one adapter per ATS: greenhouse, lever, ashby, smartrecruiters, workday, oracle,
                  custom (Tesla/Amazon/Microsoft), aggregators (community listings.json feeds)
tests/            unit tests with fixture payloads (python -m unittest discover -s tests -t .)
```

## Notes and limits

* Sites without any public JSON endpoint (Apple, Meta, Google, L3Harris, AMD's Phenom site, iCIMS,
  Eightfold…) are covered through the community feeds, which lag by hours rather than minutes.
* Workday tenants occasionally rate-limit; the watcher retries with back-off and simply picks the
  posting up on the next cycle.
* Polling every 5 minutes across ~500 boards is a few thousand light JSON requests per hour; boards
  are public career APIs designed for this traffic. Don't go much below `interval_minutes: 3`.
