"""24/7 hardware-internship watcher.

Polls company job boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workday,
Oracle HCM, a few custom career sites) plus community-maintained internship
feeds, keeps only new hardware internship postings for the configured term
(Summer 2027 by default) and pushes them to Discord / Telegram / Slack / ntfy /
email the moment they appear.
"""

__version__ = "1.0.0"
