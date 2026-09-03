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

**Option B - zero infrastructure (GitHub Actions, the default here):** `.github/workflows/hardware-internships.yml`
runs the watcher **continuously**: each job loops for about 5.5 hours (GitHub's per-job limit is 6), saves
its memory of seen postings to the Actions cache, then dispatches the next run of itself. An hourly cron is
only a watchdog that restarts the chain if a run dies. This avoids GitHub's cron scheduler, which routinely
skips short intervals. Add your secrets under *Settings → Secrets and variables → Actions*
(`TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, …), then start the chain once from the Actions tab with
"Run workflow". `run.interval_minutes` in `config.yaml` sets the polling cadence inside the loop.

## The LLM judge (why the junk stops)

Keywords only pre-filter. Every posting they accept is then read by Claude (`claude-opus-5`, low effort,
structured JSON output) together with the **full job description**, which the watcher fetches from the
ATS even for community-feed hits. The judge answers: is it really an internship, what is the day-to-day
work, is a bachelor's student eligible, does it require citizenship/clearance, and a 0-100 fit for the
profile in `config.yaml` (`llm.profile`, written from the resume, edit it freely). A `reject` verdict or
low hardware relevance drops the posting; otherwise the fit score (75%) plus the keyword score (25%)
sets the tier, and the judge's one-line summary is shown in the notification.

* Needs `ANTHROPIC_API_KEY` (console.anthropic.com → API keys) in `.env` or as a repository secret.
  Without it the watcher silently falls back to keyword tiers and flags postings `llm-unjudged`.
* Cost: roughly one to two cents per judged posting; judgments are cached so nothing is paid twice.
  Expect a few dollars for the very first run and cents per day after that.
* `python -m hwintern judge <posting URL>` shows the verdict for any single posting, handy for tuning
  the profile text.

## Tiers: what buzzes your phone and what waits for the digest

Every accepted posting gets a **fit score (0-100)** from: ⭐ priority keyword (+25), strong hardware
category such as embedded/electrical/robotics/RF (+10, silicon +5, mechanical/validation-only −10),
Summer 2027 (+15; unstated term +5; another season −10), US location (+5), and penalties for
"no sponsorship" (−15), graduate-only (−20), unknown location (−5).

| Tier | Score | Delivery |
|---|---|---|
| 🎯 **TARGET** | ≥ 75 | pushed immediately, with sound |
| ✅ **MATCH** | 55-74 | pushed immediately, with sound |
| 🟡 **SAFETY** | < 55 | collected and sent once a day as one silent digest (13:00 UTC = 9am New York), or earlier if 40 pile up |

Change the thresholds under `filters.tier_*_min`, the delivery policy under `run.digest_*`, and which
tiers arrive silently on Telegram under the notifier's `silent_tiers`. `python -m hwintern digest`
shows what is waiting; `--flush` sends it now.

## Setting up the phone channels

**Telegram (primary):**
1. In Telegram talk to `@BotFather`, send `/newbot`, copy the token it gives you.
2. Open your new bot's chat and press **Start** (or send it any message). That is all: the watcher
   reads the chat id from that message on its first run and remembers it.
3. Put the token in `.env` as `TELEGRAM_BOT_TOKEN=` (or as the GitHub Actions secret of the same name).
4. `python -m hwintern test-notify` prints a diagnosis line for Telegram, e.g.
   `telegram: bot @yourbot (id 123) ok; chat id 456` and then delivers a test message.
   * "token rejected (401)": the token in `.env` is wrong; paste the full `123456789:AA...` string.
   * "no chat id yet": you have not pressed Start on the bot; do that and re-run.
   * A stale webhook that blocks `getUpdates` is removed automatically.
   `TELEGRAM_CHAT_ID` can also be set explicitly.

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
python -m hwintern digest [--flush]                 # what is waiting for the daily digest / send it now
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
