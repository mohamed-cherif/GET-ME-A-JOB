#!/usr/bin/env python3
"""Regenerate companies.yaml from mined aggregator data + a curated list of well-known boards.

    python scripts/build_companies.py > companies.yaml

`mined_boards.json` was produced by mining the SimplifyJobs / vanshb03 internship feeds for
every hardware-flavoured posting and recording which ATS board it lived on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
mined = json.loads((HERE / "mined_boards.json").read_text())

# Companies that showed up in the feeds but are not hardware employers worth polling every few minutes.
DENY_COMPANIES = {
    "american express", "university of wyoming", "cirque du soleil", "pennsylvania state university",
    "drw", "imc trading", "optiver", "hudl", "workstream", "protech automotive solutions", "csx",
    "10beauty", "comstock companies", "vsc fire & security", "amarok security", "pivotal software",
    "eurofins", "veolia", "société générale de surveillance (sgs)", "nbcuniversal", "stem expert",
    "usm business systems", "mat holdings", "radius limited", "intertek", "dnv", "seaspan",
    "pella corporation", "norfolk southern", "the walt disney company", "snap", "snapchat",
}

# Curated, well-known boards that the feeds may not have surfaced (kind, id/host|tenant|site, company).
CURATED = {
    "greenhouse": [
        ("spacex", "SpaceX"), ("andurilindustries", "Anduril"), ("figureai", "Figure AI"),
        ("neuralink", "Neuralink"), ("rocketlab", "Rocket Lab"), ("cerebras", "Cerebras"),
        ("groq", "Groq"), ("nuro", "Nuro"), ("waymo", "Waymo"), ("appliedintuition", "Applied Intuition"),
        ("relativity", "Relativity Space"), ("jobyaviation", "Joby Aviation"), ("archer", "Archer Aviation"),
        ("bostondynamics", "Boston Dynamics"), ("agilityrobotics", "Agility Robotics"),
        ("flyzipline", "Zipline"), ("sifive", "SiFive"), ("motional", "Motional"),
        ("sambanovasystems", "SambaNova"), ("tenstorrent", "Tenstorrent"), ("lightmatter", "Lightmatter"),
        ("astranis", "Astranis"), ("vardaspace", "Varda Space"), ("trueanomalyinc", "True Anomaly"),
        ("k2spacecorporation", "K2 Space"), ("apptronik", "Apptronik"), ("kodiak", "Kodiak Robotics"),
        ("lucidmotors", "Lucid Motors"), ("samsungsemiconductor", "Samsung Semiconductor"),
        ("skhynixamerica", "SK hynix America"), ("asteralabs", "Astera Labs"), ("impinjexternal", "Impinj"),
        ("verkada", "Verkada"), ("redwoodmaterials", "Redwood Materials"), ("planetlabs", "Planet"),
    ],
    "lever": [
        ("zoox", "Zoox"), ("cirrus", "Cirrus Logic"), ("shieldai", "Shield AI"), ("saronic", "Saronic"),
        ("hermeus", "Hermeus"), ("CesiumAstro", "CesiumAstro"), ("kepler", "Kepler Communications"),
        ("plus-2", "PlusAI"), ("woven-by-toyota", "Woven by Toyota"),
    ],
    "ashby": [
        ("Etched", "Etched"), ("skydio", "Skydio"), ("d-Matrix", "d-Matrix"), ("rivianvw.tech", "Rivian & VW Group Technologies"),
        ("physicalintelligence", "Physical Intelligence"), ("1x", "1X"), ("atomicsemi", "Atomic Semi"),
        ("base-power", "Base Power"), ("eightsleep", "Eight Sleep"), ("NorthwoodSpace", "Northwood Space"),
        ("heron-power", "Heron Power"),
    ],
    "smartrecruiters": [
        ("WesternDigital", "Western Digital"), ("Sandisk", "Sandisk"), ("BoschGroup", "Bosch"),
        ("GDMSI", "General Dynamics Mission Systems"), ("Intuitive", "Intuitive Surgical"),
        ("AristaNetworks", "Arista Networks"), ("Kioxia", "Kioxia"), ("Wabtec", "Wabtec"),
        ("LLNL", "Lawrence Livermore National Laboratory"),
    ],
    "workday": [
        ("nvidia.wd5.myworkdayjobs.com|nvidia|NVIDIAExternalCareerSite", "NVIDIA"),
        ("qualcomm.wd5.myworkdayjobs.com|qualcomm|External", "Qualcomm"),
        ("broadcom.wd1.myworkdayjobs.com|broadcom|External_Career", "Broadcom"),
        ("amd.wd1.myworkdayjobs.com|amd|External", "AMD"),
        ("intel.wd1.myworkdayjobs.com|intel|External", "Intel"),
        ("micron.wd1.myworkdayjobs.com|micron|External", "Micron Technology"),
        ("marvell.wd1.myworkdayjobs.com|marvell|MarvellCareers2", "Marvell"),
        ("analogdevices.wd1.myworkdayjobs.com|analogdevices|External", "Analog Devices"),
        ("nxp.wd3.myworkdayjobs.com|nxp|careers", "NXP Semiconductors"),
        ("cadence.wd1.myworkdayjobs.com|cadence|External_Careers", "Cadence Design Systems"),
        ("cadence.wd1.myworkdayjobs.com|cadence|Univ_Careers", "Cadence Design Systems (University)"),
        ("amat.wd1.myworkdayjobs.com|amat|External", "Applied Materials"),
        ("kla.wd1.myworkdayjobs.com|kla|UR", "KLA (University)"),
        ("kla.wd1.myworkdayjobs.com|kla|search", "KLA"),
        ("lamresearch.wd1.myworkdayjobs.com|lamresearch|LamCareers", "Lam Research"),
        ("globalfoundries.wd1.myworkdayjobs.com|globalfoundries|External", "GlobalFoundries"),
        ("altera.wd1.myworkdayjobs.com|altera|altera", "Altera"),
        ("ambarella.wd108.myworkdayjobs.com|ambarella|ambarella", "Ambarella"),
        ("lumentum.wd5.myworkdayjobs.com|lumentum|LITE", "Lumentum"),
        ("ciena.wd5.myworkdayjobs.com|ciena|Careers", "Ciena"),
        ("wd5.myworkdaysite.com|microchiphr|External", "Microchip Technology"),
        ("wd3.myworkdaysite.com|magna|Magna", "Magna"),
        ("globalhr.wd5.myworkdayjobs.com|globalhr|rec_rtx_ext_gateway", "RTX (Raytheon / Collins / Pratt & Whitney)"),
        ("ngc.wd1.myworkdayjobs.com|ngc|Northrop_Grumman_External_Site", "Northrop Grumman"),
        ("boeing.wd1.myworkdayjobs.com|boeing|EXTERNAL_CAREERS", "Boeing"),
        ("blueorigin.wd5.myworkdayjobs.com|blueorigin|blueorigin", "Blue Origin"),
        ("geaerospace.wd5.myworkdayjobs.com|geaerospace|ge_externalsite", "GE Aerospace"),
        ("gevernova.wd5.myworkdayjobs.com|gevernova|vernova_externalsite", "GE Vernova"),
        ("gehc.wd5.myworkdayjobs.com|gehc|GEHC_ExternalSite", "GE HealthCare"),
        ("draper.wd5.myworkdayjobs.com|draper|Draper_Careers", "Draper"),
        ("moog.wd5.myworkdayjobs.com|moog|moog_external_career_site", "Moog"),
        ("leidos.wd5.myworkdayjobs.com|leidos|External", "Leidos"),
        ("motorolasolutions.wd5.myworkdayjobs.com|motorolasolutions|Careers", "Motorola Solutions"),
        ("generalmotors.wd5.myworkdayjobs.com|generalmotors|Careers_GM", "General Motors"),
        ("aptiv.wd5.myworkdayjobs.com|aptiv|aptiv_careers", "Aptiv"),
        ("borgwarner.wd5.myworkdayjobs.com|borgwarner|BorgWarner_Careers", "BorgWarner"),
        ("selinc.wd1.myworkdayjobs.com|selinc|SEL", "Schweitzer Engineering Laboratories"),
        ("medtronic.wd1.myworkdayjobs.com|medtronic|MedtronicCareers", "Medtronic"),
        ("jj.wd5.myworkdayjobs.com|jj|JJ", "Johnson & Johnson"),
        ("hp.wd5.myworkdayjobs.com|hp|EXTEU-AC-CareerSite", "HP"),
        ("harman.wd3.myworkdayjobs.com|harman|HARMAN", "HARMAN"),
        ("boseallaboutme.wd503.myworkdayjobs.com|boseallaboutme|Bose_Careers", "Bose"),
        ("flir.wd1.myworkdayjobs.com|flir|flircareers", "Teledyne FLIR"),
        ("abb.wd3.myworkdayjobs.com|abb|external_career_page", "ABB"),
        ("philips.wd3.myworkdayjobs.com|philips|jobs-and-careers", "Philips"),
        ("jci.wd5.myworkdayjobs.com|jci|JCI", "Johnson Controls"),
        ("thales.wd3.myworkdayjobs.com|thales|Careers", "Thales"),
        ("bb.wd3.myworkdayjobs.com|bb|Student", "BlackBerry / QNX (Students)"),
    ],
    "oracle": [
        ("edbz.fa.us2.oraclecloud.com|CX", "Texas Instruments"),
        ("hctz.fa.us2.oraclecloud.com|CX_1001", "onsemi"),
        ("ibqbjb.fa.ocs.oraclecloud.com|Honeywell", "Honeywell"),
        ("fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com|CX_1", "Nokia"),
        ("hcwp.fa.us2.oraclecloud.com|CX_1", "Coherent"),
        ("efds.fa.em5.oraclecloud.com|CX_1", "Ford Motor Company"),
        ("hdjq.fa.us2.oraclecloud.com|CX_1", "Emerson"),
        ("fa-espx-saasfaprod1.fa.ocs.oraclecloud.com|CX_1", "Cummins"),
        ("egup.fa.us2.oraclecloud.com|CX", "Vertiv"),
        ("etyy.fa.ap2.oraclecloud.com|CX_1", "Kulicke & Soffa"),
        ("hcbo.fa.us2.oraclecloud.com|CX_1", "Cohu"),
    ],
}


def entries_for(kind: str) -> list[tuple[str, str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for ident, company in CURATED.get(kind, []):
        out[ident] = (company, "curated")
    for ident, company in sorted(mined.get(kind, {}).items(), key=lambda kv: kv[1].lower()):
        if company.strip().lower() in DENY_COMPANIES:
            continue
        key = ident if kind != "greenhouse" else ident.lower()
        if key not in out:
            out[key] = (company.strip(), "feed")
    return sorted(((i, c, o) for i, (c, o) in out.items()), key=lambda t: t[1].lower())


def q(s: str) -> str:
    return json.dumps(s)


lines = [
    "# Boards polled directly. Generated by scripts/build_companies.py - edit freely, or add boards",
    "# at runtime with `python -m hwintern add-board --url <any job URL>`.",
    "#",
    "# kinds: greenhouse | lever | ashby | smartrecruiters | workday | oracle | tesla | amazon | microsoft",
    "#   id for workday = \"host|tenant|site\", for oracle = \"host|siteNumber\" (or give host/tenant/site keys).",
    "# origin: curated = hand-picked well-known board; feed = discovered from community internship feeds.",
    "",
    "companies:",
    "  # --- custom career sites -------------------------------------------------",
    "  - {kind: tesla, company: Tesla}",
    "  - {kind: amazon, company: Amazon}",
    "  - {kind: microsoft, company: Microsoft}",
]
for kind in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday", "oracle"):
    rows = entries_for(kind)
    lines.append("")
    lines.append(f"  # --- {kind} ({len(rows)}) " + "-" * max(1, 60 - len(kind)))
    for ident, company, origin in rows:
        lines.append(f"  - {{kind: {kind}, id: {q(ident)}, company: {q(company)}, origin: {origin}}}")
sys.stdout.write("\n".join(lines) + "\n")
