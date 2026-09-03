"""Classification: is this posting a hardware internship for the term we want?"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .geo import countries_for, is_remote
from .models import Job

# ---------------------------------------------------------------------------
# Hardware taxonomy. Each category is a list of regexes matched (case-insensitively)
# against the title, and, when enabled, the description. Titles are strong
# evidence; descriptions need at least `min_description_hits` distinct
# categories/keywords to count.
# ---------------------------------------------------------------------------
HARDWARE_CATEGORIES: dict[str, list[str]] = {
    "electrical": [
        r"\belectrical\b", r"\bEE\b", r"\bECE\b", r"\belectronics?\b", r"\bcircuits?\b",
        r"\bpower electronics\b", r"\bpower systems?\b", r"\bpower conversion\b",
        r"\bbattery\b", r"\bBMS\b", r"\bmotor (?:control|drive)s?\b", r"\bhigh[- ]voltage\b",
    ],
    "hardware": [
        r"\bhardware\b", r"\bHW\b", r"\bhw/sw\b", r"\bboard[- ]level\b", r"\bboard design\b",
        r"\bPCB\b", r"\bPCBA\b", r"\bschematic\b", r"\blayout\b", r"\bsignal integrity\b",
        r"\bpower integrity\b", r"\bEMC\b", r"\bEMI\b", r"\bcompliance\b",
        r"\bhardware (?:test|validation|bring[- ]?up)\b", r"\bproduct design\b",
    ],
    "embedded": [
        r"\bembedded\b", r"\bfirmware\b", r"\bFW\b", r"\bRTOS\b", r"\bbare[- ]metal\b",
        r"\bmicrocontroller\b", r"\bMCU\b", r"\bdevice drivers?\b", r"\bBSP\b",
        r"\bbootloader\b", r"\bbring[- ]?up\b", r"\bkernel\b", r"\bIoT\b",
    ],
    "silicon": [
        r"\bASIC\b", r"\bFPGA\b", r"\bRTL\b", r"\bVLSI\b", r"\bSoC\b", r"\bsilicon\b",
        r"\bchip\b", r"\bsemiconductor\b", r"\bdigital design\b", r"\bdigital (?:ic|logic)\b",
        r"\bdesign verification\b", r"\bverification\b", r"\bUVM\b",
        r"\bDFT\b", r"\bphysical design\b", r"\bplace and route\b", r"\bSTA\b",
        r"\btiming\b", r"\bsynthesis\b", r"\bIP design\b", r"\bCPU\b", r"\bGPU\b",
        r"\bNPU\b", r"\bTPU\b", r"\baccelerator\b", r"\bmicroarchitecture\b",
        r"\bcomputer architecture\b", r"\bprocessor\b", r"\bmemory design\b", r"\bSRAM\b",
        r"\bDRAM\b", r"\bNAND\b", r"\bpost[- ]silicon\b", r"\bpre[- ]silicon\b",
        r"\bemulation\b", r"\bsilicon validation\b", r"\bfab\b", r"\bprocess (?:engineer|integration|development)\b",
        r"\blithography\b", r"\bdevice engineer\b", r"\bpackaging\b", r"\byield\b",
        r"\bwafer\b", r"\bTCAD\b", r"\bEDA\b", r"\bCAD engineer\b", r"\bfailure analysis\b",
    ],
    "analog_rf": [
        r"\banalog\b", r"\bmixed[- ]signal\b", r"\bAMS\b", r"\bRF\b", r"\bRFIC\b",
        r"\bmicrowave\b", r"\bmmWave\b", r"\bantenna\b", r"\bwireless\b", r"\bradio\b",
        r"\bradar\b", r"\bSerDes\b", r"\bhigh[- ]speed\b", r"\bPLL\b", r"\bADC\b", r"\bDAC\b",
        r"\btransceiver\b", r"\bphased array\b", r"\bRF/analog\b", r"\bphotonics?\b",
        r"\boptical\b", r"\boptics\b", r"\blaser\b", r"\bLiDAR\b", r"\bsensor\b", r"\bimaging\b",
        r"\bcamera\b", r"\bsignal processing\b", r"\bDSP\b",
    ],
    "robotics_controls": [
        r"\brobot(?:ics?|ic)\b", r"\bmechatronics?\b", r"\bcontrols?\b", r"\bcontrol systems?\b",
        r"\bGN&?C\b", r"\bguidance\b", r"\bnavigation\b", r"\bautonomy\b", r"\bmotion planning\b",
        r"\bperception\b", r"\bactuat(?:or|ion)\b", r"\bautonomous\b", r"\bdrone\b", r"\bUAV\b",
        r"\bavionics\b", r"\bflight (?:software|controls?|systems?)\b", r"\bUAS\b", r"\beVTOL\b",
        r"\baircraft\b", r"\bsatellite\b", r"\bspacecraft\b", r"\blaunch vehicle\b", r"\brocket\b",
        r"\bmaritime\b", r"\bnaval\b", r"\bships?\b", r"\bshipboard\b", r"\bmarine\b", r"\bunderwater\b",
        r"\bsubsea\b", r"\bUUV\b", r"\bAUV\b", r"\bUSV\b", r"\bROV\b", r"\bexoskeleton\b",
        r"\bhumanoid\b", r"\bmanipulation\b", r"\bSLAM\b", r"\bstate estimation\b", r"\blocalization\b",
        r"\bkinematics\b", r"\bmotion control\b",
    ],
    "mechanical": [
        r"\bmechanical\b", r"\bME\b", r"\bmech\b", r"\bthermal\b", r"\bstructur(?:al|es)\b",
        r"\bCAD\b", r"\bFEA\b", r"\bCFD\b", r"\bpropulsion\b", r"\bmanufacturing\b",
        r"\bindustrial\b", r"\bDFM\b", r"\btooling\b", r"\bmaterials?\b", r"\bpackaging\b",
        r"\baerospace\b", r"\baeronautic", r"\bfluids?\b", r"\bHVAC\b", r"\bautomotive\b",
        r"\bchassis\b", r"\bpowertrain\b", r"\benclosure\b",
    ],
    "test_validation": [
        r"\btest engineer", r"\bvalidation\b", r"\bATE\b", r"\bDVT\b", r"\bEVT\b", r"\bPVT\b",
        r"\breliability\b", r"\bqualification\b", r"\bcharacterization\b", r"\btest automation\b",
        r"\bproduct (?:test|validation)\b", r"\bNPI\b",
    ],
}

INTERN_RE = re.compile(
    r"\b(intern(?:ship|s)?|co[- ]?op(?:erative)?(?: education)?|student (?:engineer|technician|worker|program)|"
    r"summer (?:student|analyst|program|scholar|technical)|placement|werkstudent|stagiaire|alternance|"
    r"industrial placement|undergraduate researcher|apprentice(?:ship)?)\b",
    re.I,
)

# Titles we never want even if they contain "intern".
DEFAULT_EXCLUDE_TITLE = [
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of|vp|vice president|chief)\b",
    r"\b(internal|international)\b(?!.*\bintern\b)",  # "Internal Audit", "International Sales"
    r"\b(sales|marketing|finance|accounting|hr|human resources|legal|recruit(?:ing|er)|talent|"
    r"communications|copywriter|graphic design|ux|ui design|customer success|account executive|"
    r"business development|supply chain analyst|procurement|payroll|nursing|pharmacy|clinical)\b",
    r"\bnew ?grad\b", r"\bfull[- ]time\b(?!.*\b(intern|co-?op)\b)", r"\bMBA\b",
    r"\b(analyst|consultant|consulting|technical writer|instructor|tutor|teaching assistant|lifeguard|"
    r"installation technician|skillbridge)\b",
]

# Pure software roles are excluded unless a hardware keyword also matches.
SOFTWARE_ONLY_HINTS = re.compile(
    r"\b(software|swe|sde|full[- ]?stack|front[- ]?end|back[- ]?end|web|mobile|ios|android|"
    r"data (?:science|scientist|analyst|engineer)|machine learning|ml|ai|cloud|devops|sre|"
    r"security|cyber|product manager|program manager|analytics|business|it support|qa)\b", re.I)

SEASON_RE = re.compile(
    r"\b(summer|fall|autumn|spring|winter|sommer)\s*(?:of\s*|['’]?)?(20\d{2}|'\d{2})\b", re.I)
# "Spring/Summer 2027", "Summer or Fall 2027": the first season borrows the year of the second
SEASON_PAIR_RE = re.compile(
    r"\b(summer|fall|autumn|spring|winter)\s*(?:/|&|,|and|or|-|–)\s*(?:summer|fall|autumn|spring|winter)\s*(?:of\s*)?(20\d{2})\b", re.I)
YEAR_RE = re.compile(r"\b(20[2-3]\d)\b")
YEAR_RANGE_RE = re.compile(r"\b(20[2-3]\d)\s*[-/–]\s*(20[2-3]\d|\d{2})\b")
SEASON_ALIAS = {"autumn": "fall", "sommer": "summer"}

FLAG_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("citizenship-required", re.compile(
        r"\b(u\.?s\.? (?:citizen(?:ship)?|person)s? (?:is|are|status)? ?(?:required|only)|must be (?:a )?u\.?s\.? citizen|"
        r"citizenship (?:is )?required|u\.?s\.? citizens? only|ITAR|export control|"
        r"(?:secret|top secret|ts/sci|security) clearance|clearance (?:is )?required|"
        r"must be (?:a )?(?:u\.?s\.?|united states) (?:citizen|person)|"
        r"(?:citizens?|citizenship) (?:or|and) (?:lawful |legal )?permanent residents?|"
        r"(?:permanent resident|green card)(?: holder)?s? (?:status )?(?:is |are )?(?:required|only)|"
        r"must be (?:a )?(?:(?:lawful |legal )?permanent resident|green card holder)|"
        r"u\.?s\.? person(?:s)? (?:status )?(?:as defined|under|per)|requires? u\.?s\.? person)\b", re.I)),
    ("phd-title", re.compile(r"\b(ph\.?\s?d\.?|doctoral|doctorate)\b", re.I)),   # only evaluated on the title
    ("grad-only", re.compile(
        r"\b(?:(?:currently )?(?:pursuing|enrolled in|working toward[s]?|in) (?:a |an )?(?:master['’]?s|m\.?s\.?|msc|ph\.?d\.?|"
        r"doctoral|graduate) (?:degree|program|student|candidate)|graduate students? only|"
        r"(?:master['’]?s|ph\.?d\.?) (?:students?|candidates?) (?:only|required|preferred))\b", re.I)),
    ("no-sponsorship", re.compile(
        r"\b(no (?:visa )?sponsorship|(?:will not|does not|unable to|cannot|not able to) (?:offer|provide|sponsor)[^.]{0,60}"
        r"(?:sponsorship|visa)|without (?:the need for )?(?:visa )?sponsorship|"
        r"(?:not|unable to) (?:sponsor|support) (?:employment )?visas?|sponsorship (?:is )?not (?:available|offered))\b", re.I)),
    ("sponsorship-offered", re.compile(r"\b(visa sponsorship (?:is )?(?:available|offered|provided)|will sponsor|offers? sponsorship)\b", re.I)),
    ("phd", re.compile(r"\b(ph\.?d\.?|doctoral)\b", re.I)),
    ("masters", re.compile(r"\b(master['’]?s|m\.?s\.?|msc|meng)\b", re.I)),
    ("returning-students", re.compile(r"\breturning (?:student|intern)s?\b", re.I)),
]

UNDERGRAD_RE = re.compile(r"\b(undergraduate|undergrad|bachelor['’]?s?|b\.?s\.?(?:c)?\b|BSEE|BSCE|BSME|sophomore|junior|rising)\b", re.I)
TITLE_ONLY_FLAGS = {"phd-title"}


def _compile(patterns: Iterable[str]) -> list[re.Pattern]:
    out = []
    for p in patterns:
        flags = 0 if not re.search(r"[a-z]", p.replace("\\b", "")) else re.I
        out.append(re.compile(p, flags))
    return out


@dataclass
class FilterConfig:
    target_terms: list[str] = field(default_factory=lambda: ["Summer 2027"])
    also_accept_terms: list[str] = field(default_factory=list)   # e.g. ["Spring 2027", "Fall 2027"]
    reject_years: list[int] = field(default_factory=lambda: [2023, 2024, 2025, 2026])
    accept_unknown_term: bool = True
    accept_other_terms: bool = False   # keep postings for other seasons (flagged "other-term"), unless in reject_terms
    reject_terms: list[str] = field(default_factory=list)   # e.g. ["Spring 2026", "Summer 2026"]
    priority_keywords: list[str] = field(default_factory=list)  # title regexes that mark a posting "priority"
    categories: list[str] = field(default_factory=lambda: list(HARDWARE_CATEGORIES.keys()))
    extra_keywords: list[str] = field(default_factory=list)
    exclude_title: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_TITLE))
    match_description: bool = True
    min_description_hits: int = 2
    require_internship: bool = True
    location_include: list[str] = field(default_factory=list)
    location_exclude: list[str] = field(default_factory=list)
    # ISO codes. When set, a posting whose location resolves to a country outside the list is dropped;
    # an unresolvable location ("Remote", "Multiple locations") is kept and flagged "location-unknown".
    countries_allow: list[str] = field(default_factory=list)
    drop_unknown_location: bool = False
    # Fit-score thresholds (0-100) that split accepted postings into tiers.
    tier_target_min: int = 75
    tier_match_min: int = 55
    preferred_countries: list[str] = field(default_factory=lambda: ["US"])   # small score bonus (CPT is simplest)
    exclude_sponsorship: list[str] = field(default_factory=list)  # aggregator labels, e.g. "U.S. Citizenship is Required"
    exclude_flags: list[str] = field(default_factory=list)        # e.g. ["citizenship-required"]
    trust_aggregator_category: bool = True
    exclude_companies: list[str] = field(default_factory=list)   # regexes, e.g. ["\\bUniversity\\b"]
    # A title that reads as software ("Software Engineer Intern - X") is only kept when X hits one of these.
    software_title_categories: list[str] = field(
        default_factory=lambda: ["electrical", "hardware", "embedded", "silicon", "analog_rf", "robotics_controls"])

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "FilterConfig":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Verdict:
    accepted: bool
    reason: str
    needs_description: bool = False   # would accept if a description confirmed hardware / term
    categories: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    score: int = 0
    tier: str = "safety"


class Classifier:
    def __init__(self, cfg: FilterConfig):
        self.cfg = cfg
        self.cat_patterns = {c: _compile(HARDWARE_CATEGORIES[c]) for c in cfg.categories if c in HARDWARE_CATEGORIES}
        if cfg.extra_keywords:
            self.cat_patterns["custom"] = _compile(cfg.extra_keywords)
        self.exclude_title = _compile(cfg.exclude_title)
        self.loc_inc = _compile(cfg.location_include)
        self.loc_exc = _compile(cfg.location_exclude)
        self.exclude_companies = _compile(cfg.exclude_companies)
        self.targets = {t.lower() for t in cfg.target_terms + cfg.also_accept_terms}
        self.primary = {t.lower() for t in cfg.target_terms}
        self.reject_terms = {t.lower() for t in cfg.reject_terms}
        self.priority = _compile(cfg.priority_keywords)
        self.target_years = {int(m.group(1)) for t in self.targets for m in [YEAR_RE.search(t)] if m}
        self.reject_years = set(cfg.reject_years) - self.target_years

    # -- hardware -----------------------------------------------------------
    def hardware_categories(self, text: str) -> list[str]:
        hits = []
        for cat, pats in self.cat_patterns.items():
            if any(p.search(text) for p in pats):
                hits.append(cat)
        return hits

    def description_hits(self, text: str) -> tuple[list[str], int]:
        cats, n = [], 0
        for cat, pats in self.cat_patterns.items():
            k = sum(1 for p in pats if p.search(text))
            if k:
                cats.append(cat)
                n += k
        return cats, n

    # -- term ---------------------------------------------------------------
    def detect_terms(self, text: str) -> tuple[list[str], set[int]]:
        terms: list[str] = []
        years: set[int] = set()
        for m in SEASON_RE.finditer(text):
            season = SEASON_ALIAS.get(m.group(1).lower(), m.group(1).lower())
            y = m.group(2)
            year = int("20" + y[-2:]) if len(y) <= 3 else int(y)
            terms.append(f"{season.capitalize()} {year}")
            years.add(year)
        for m in SEASON_PAIR_RE.finditer(text):
            season = SEASON_ALIAS.get(m.group(1).lower(), m.group(1).lower())
            terms.append(f"{season.capitalize()} {int(m.group(2))}")
        for m in YEAR_RANGE_RE.finditer(text):
            a, b = m.group(1), m.group(2)
            years.add(int(a))
            years.add(int(b) if len(b) == 4 else int(a[:2] + b))
        for m in YEAR_RE.finditer(text):
            years.add(int(m.group(1)))
        return sorted(set(terms)), years

    def term_ok(self, job: Job, text: str) -> tuple[Optional[bool], list[str], bool]:
        """Returns (ok|None for unknown, detected_terms, from_aggregator)."""
        # feed terms without a year ("Summer", "Fall") carry no usable information; fall back to the text
        agg_terms = [t for t in job.terms if t and t.upper() != "N/A" and YEAR_RE.search(t)]
        if agg_terms:
            if any(t.lower() in self.targets for t in agg_terms):
                return True, agg_terms, True
            # aggregator says a different term; still accept if the text itself names ours
            detected, _ = self.detect_terms(text)
            if any(t.lower() in self.targets for t in detected):
                return True, detected, False
            return self._other_term(agg_terms), agg_terms, True
        detected, years = self.detect_terms(text)
        if any(t.lower() in self.targets for t in detected):
            return True, detected, False
        if detected:  # an explicit season that is not ours
            return self._other_term(detected), detected, False
        if years & self.target_years:
            return True, [str(y) for y in sorted(years)], False
        if years & self.reject_years:
            return False, [str(y) for y in sorted(years)], False
        return None, [], False

    def _other_term(self, terms: list[str]) -> bool:
        """Explicit season that is not a target: keep only when accept_other_terms and not rejected."""
        if not self.cfg.accept_other_terms:
            return False
        for t in terms:
            tl = t.lower()
            if tl in self.reject_terms:
                return False
            m = YEAR_RE.search(t)
            if m and int(m.group(1)) in self.reject_years:
                return False
        return True

    def is_primary_term(self, terms: list[str]) -> bool:
        return any(t.lower() in self.primary for t in terms)

    # -- location / sponsorship -------------------------------------------
    def location_ok(self, location: str) -> bool:
        loc = location or ""
        if self.loc_inc and not any(p.search(loc) for p in self.loc_inc):
            return False
        if self.loc_exc and any(p.search(loc) for p in self.loc_exc):
            return False
        return True

    def country_check(self, location: str) -> tuple[Optional[bool], set[str]]:
        """(ok | None when unknown, resolved country codes)."""
        found = countries_for(location)
        if not self.cfg.countries_allow:
            return True, found
        if not found:
            return None, found
        allow = {c.upper() for c in self.cfg.countries_allow}
        return bool(found & allow), found

    def flags_for(self, text: str, title: str = "") -> list[str]:
        out = []
        for name, pat in FLAG_PATTERNS:
            if name in TITLE_ONLY_FLAGS:
                if pat.search(title):
                    out.append(name)
            elif pat.search(text):
                out.append(name)
        if "grad-only" in out and UNDERGRAD_RE.search(text):
            out.remove("grad-only")   # undergrads are mentioned too, so it is not graduate-only
        if self.priority and any(p.search(title) for p in self.priority):
            out.insert(0, "priority")
        return out

    # -- main ---------------------------------------------------------------
    def classify(self, job: Job) -> Verdict:
        title = job.title or ""
        desc = job.description or ""
        title_text = title
        full_text = f"{title}\n{desc}"

        for p in self.exclude_title:
            if p.search(title_text):
                return Verdict(False, f"excluded-title:{p.pattern[:30]}")
        for p in self.exclude_companies:
            if p.search(job.company or ""):
                return Verdict(False, "excluded-company")

        if self.cfg.require_internship and not INTERN_RE.search(title_text):
            # Aggregator feeds are internship-only by construction.
            if not job.terms and not (job.category and self.cfg.trust_aggregator_category):
                if not (desc and INTERN_RE.search(desc[:400])):
                    return Verdict(False, "not-internship")

        if not self.location_ok(job.location):
            return Verdict(False, "location")
        country_ok, countries = self.country_check(job.location)
        if country_ok is False:
            return Verdict(False, f"country:{','.join(sorted(countries))}")
        if country_ok is None and self.cfg.drop_unknown_location:
            return Verdict(False, "location-unknown")
        if job.sponsorship and job.sponsorship in self.cfg.exclude_sponsorship:
            return Verdict(False, f"sponsorship:{job.sponsorship}")

        # hardware?
        cats = self.hardware_categories(title_text)
        if cats and SOFTWARE_ONLY_HINTS.search(title_text):
            strong = [c for c in cats if c in self.cfg.software_title_categories or c == "custom"]
            if not strong:
                return Verdict(False, f"software-title:{','.join(cats)}")
            cats = strong
        # Community feeds label whole companies as "Hardware", so only trust that label when the
        # title is not obviously a pure-software / non-engineering role.
        agg_hw = self.cfg.trust_aggregator_category and job.category.lower().startswith("hardware")
        if agg_hw and not cats and not SOFTWARE_ONLY_HINTS.search(title_text):
            cats = ["feed:hardware"]
        needs_desc = False
        if not cats:
            if desc and self.cfg.match_description:
                dcats, n = self.description_hits(desc)
                if n >= self.cfg.min_description_hits and not SOFTWARE_ONLY_HINTS.search(title_text):
                    cats = [f"desc:{c}" for c in dcats]
                elif n >= self.cfg.min_description_hits + 2:
                    cats = [f"desc:{c}" for c in dcats]
            elif not desc and not job.has_full_description and self.cfg.match_description:
                needs_desc = True
        if not cats:
            return Verdict(False, "not-hardware", needs_description=needs_desc)

        # term?
        ok, terms, _ = self.term_ok(job, full_text)
        if ok is False:
            return Verdict(False, f"term:{','.join(terms) or '?'}", categories=cats, terms=terms)
        if ok is None:
            if not desc and not job.has_full_description:
                needs_desc = True
            if not self.cfg.accept_unknown_term:
                return Verdict(False, "term-unknown", needs_description=needs_desc, categories=cats)

        flags = self.flags_for(full_text, title_text)
        if ok is None:
            flags.append("term-unknown")
        elif terms and not self.is_primary_term(terms) and not any(YEAR_RE.fullmatch(t) for t in terms):
            flags.append("other-term")
        if country_ok is None:
            flags.append("location-unknown")
        if is_remote(job.location):
            flags.append("remote")
        for f in self.cfg.exclude_flags:
            if f in flags:
                return Verdict(False, f"flag:{f}", categories=cats, terms=terms, flags=flags)
        score = self.fit_score(cats, flags, countries, ok)
        return Verdict(True, "ok", needs_description=False, categories=cats, terms=terms, flags=flags,
                       score=score, tier=self.tier_for(score))

    # -- fit score ------------------------------------------------------------
    STRONG_CATS = {"embedded", "electrical", "hardware", "robotics_controls", "analog_rf", "custom"}

    def fit_score(self, cats: list[str], flags: list[str], countries: set[str], term_ok: Optional[bool]) -> int:
        """0-100: how well the posting fits the profile. Drives the tier and notification priority."""
        score = 40
        base_cats = {c.split(":", 1)[-1] for c in cats}
        if "priority" in flags:
            score += 25
        if base_cats & self.STRONG_CATS:
            score += 10
        elif "silicon" in base_cats:
            score += 5
        elif base_cats and base_cats <= {"mechanical", "test_validation"}:
            score -= 10         # mechanical / validation only: kept, but for the digest
        if any(c.startswith("desc:") for c in cats):
            score -= 5          # hardware only inferred from the description
        if term_ok is True and "other-term" not in flags:
            score += 15         # the primary term
        elif term_ok is None:
            score += 5
        else:
            score -= 10         # another season
        if countries & {c.upper() for c in self.cfg.preferred_countries}:
            score += 5
        if "location-unknown" in flags:
            score -= 5
        if "no-sponsorship" in flags:
            score -= 15
        if "grad-only" in flags:
            score -= 20
        elif "masters" in flags and "phd" in flags:
            score -= 5
        if "citizenship-required" in flags:
            score -= 30
        return max(0, min(100, score))

    def tier_for(self, score: int) -> str:
        if score >= self.cfg.tier_target_min:
            return "target"
        if score >= self.cfg.tier_match_min:
            return "match"
        return "safety"
