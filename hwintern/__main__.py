from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .http import Http
from .notify import build_notifiers
from .pipeline import Pipeline
from .sources import build_source
from .sources.discovery import board_from_url
from .store import Store


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_run(args, cfg):
    p = Pipeline(cfg, dry_run=args.dry_run)
    if args.loop:
        p.run_forever()
    else:
        rep = p.run_once()
        print(rep.summary())
        if rep.notified:
            print(f"notified {len(rep.notified)} job(s)")
        return 0


def cmd_test_notify(args, cfg):
    http = Http()
    ns = build_notifiers(cfg.notifiers, http)
    if not ns:
        print("no notifiers configured (check config.yaml and env vars)", file=sys.stderr)
        return 1
    for n in ns:
        try:
            n.send_text("✅ hardware internships watcher: notifications are working.")
            print(f"{n.name}: ok")
        except Exception as exc:  # noqa: BLE001
            print(f"{n.name}: FAILED - {exc}")
    return 0


def cmd_check_board(args, cfg):
    entry = {"kind": args.kind, "id": args.id, "company": args.company or args.id}
    if args.host:
        entry["host"] = args.host
    if args.site:
        entry["site"] = args.site
    if args.tenant:
        entry["tenant"] = args.tenant
    src = build_source(Http(timeout=cfg.run.request_timeout), entry, cfg.run)
    if not src:
        return 1
    src.store = Store(cfg.db_path)
    jobs = src.fetch()
    print(f"{src.label}: {len(jobs)} posting(s)")
    from .filters import Classifier
    clf = Classifier(cfg.filters)
    shown = 0
    for j in jobs:
        v = clf.classify(j)
        tag = "MATCH" if v.accepted else ("maybe" if v.needs_description else "-")
        if args.all or v.accepted or v.needs_description:
            print(f"  [{tag:5}] {j.title} | {j.location} | {v.reason} | {j.url}")
            shown += 1
    print(f"shown {shown}/{len(jobs)}")
    return 0


def cmd_add_board(args, cfg):
    store = Store(cfg.db_path)
    if args.url:
        entry = board_from_url(args.url, args.company or "")
        if not entry:
            print("could not recognise an ATS board in that URL", file=sys.stderr)
            return 1
    else:
        entry = {"kind": args.kind, "id": args.id, "company": args.company or args.id}
    params = {k: v for k, v in entry.items() if k not in ("kind", "id", "company")}
    added = store.add_board(entry["kind"], entry["id"], entry.get("company") or entry["id"], params, origin="manual")
    print(("added " if added else "already present ") + f"{entry['kind']}:{entry['id']}")
    return 0


def cmd_remove_board(args, cfg):
    store = Store(cfg.db_path)
    print("removed" if store.remove_board(args.kind, args.id) else "not found")
    return 0


def cmd_boards(args, cfg):
    p = Pipeline(cfg, notifiers=[])
    srcs = p.sources()
    failing = {(b["kind"], b["ident"]): b for b in p.store.boards()}
    for s in sorted(srcs, key=lambda s: (s.kind, s.company.lower())):
        b = failing.get((s.kind, s.ident))
        extra = f"  (failures={b['failures']} last={b['last_error']})" if b and b.get("failures") else ""
        print(f"{s.kind:16} {s.company:40} {s.ident}{extra}")
    print(f"{len(srcs)} sources")
    return 0


def cmd_discover(args, cfg):
    """Mine the aggregator feeds for hardware postings and register their boards."""
    p = Pipeline(cfg, notifiers=[])
    known = {(s.kind, s.ident) for s in p.sources()}
    http = p.http
    found = 0
    for e in cfg.aggregators:
        src = build_source(http, e, cfg.run)
        if not src:
            continue
        src.store = p.store
        jobs = src.fetch()
        for j in jobs:
            v = p.classifier.classify(j)
            if not v.accepted:
                continue
            entry = board_from_url(j.url, j.company)
            if not entry or (entry["kind"], entry["id"]) in known:
                continue
            params = {k: v for k, v in entry.items() if k not in ("kind", "id", "company")}
            if args.apply:
                if p.store.add_board(entry["kind"], entry["id"], j.company, params, origin=f"discover:{src.ident}"):
                    known.add((entry["kind"], entry["id"]))
                    found += 1
                    print(f"added {entry['kind']}:{entry['id']} ({j.company})")
            else:
                known.add((entry["kind"], entry["id"]))
                found += 1
                print(f"{entry['kind']:16} {j.company:35} {entry['id']}")
    print(f"{found} new board(s){' added' if args.apply else ' (re-run with --apply to add them)'}")
    return 0


def cmd_export(args, cfg):
    store = Store(cfg.db_path)
    rows = store.matched_jobs(limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        print("| Found | Company | Title | Location | Terms | Flags | Link |\n|---|---|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['first_seen'][:16]} | {r['company']} | {r['title']} | {r['location']} | "
                  f"{', '.join(r.get('detected_terms') or [])} | {', '.join(r.get('flags') or [])} | [apply]({r['url']}) |")
    return 0


def cmd_stats(args, cfg):
    store = Store(cfg.db_path)
    print(json.dumps(store.stats(), indent=1))
    return 0


def cmd_reset(args, cfg):
    if cfg.db_path.exists():
        if not args.yes:
            print(f"refusing to delete {cfg.db_path} without --yes", file=sys.stderr)
            return 1
        cfg.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(str(cfg.db_path) + suffix)
            if p.exists():
                p.unlink()
        print("state reset; next run will rebuild the baseline")
    else:
        print("no state to reset")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hwintern", description="24/7 hardware internship watcher")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--companies", default=None, help="override companies file")
    ap.add_argument("--log-level", default=None)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run", help="poll every source once (or forever with --loop)")
    s.add_argument("--loop", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="classify and record, but do not notify")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("test-notify", help="send a test message on every configured channel")
    s.set_defaults(fn=cmd_test_notify)

    s = sub.add_parser("check-board", help="fetch one board live and show what would match")
    s.add_argument("kind")
    s.add_argument("id")
    s.add_argument("--company")
    s.add_argument("--host")
    s.add_argument("--site")
    s.add_argument("--tenant")
    s.add_argument("--all", action="store_true", help="show every posting, not just candidates")
    s.set_defaults(fn=cmd_check_board)

    s = sub.add_parser("add-board", help="register an extra board (by kind+id or by pasting a job URL)")
    s.add_argument("--kind")
    s.add_argument("--id")
    s.add_argument("--url")
    s.add_argument("--company")
    s.set_defaults(fn=cmd_add_board)

    s = sub.add_parser("remove-board")
    s.add_argument("kind")
    s.add_argument("id")
    s.set_defaults(fn=cmd_remove_board)

    s = sub.add_parser("boards", help="list every board that will be polled")
    s.set_defaults(fn=cmd_boards)

    s = sub.add_parser("discover", help="find new boards from the aggregator feeds")
    s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_discover)

    s = sub.add_parser("export", help="print every matched posting seen so far")
    s.add_argument("--json", action="store_true")
    s.add_argument("--limit", type=int, default=1000)
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("stats")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("reset", help="delete the state database")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_reset)

    args = ap.parse_args(argv)
    cfg = load_config(args.config, args.companies)
    _setup_logging(args.log_level or cfg.run.log_level)
    return int(args.fn(args, cfg) or 0)


if __name__ == "__main__":
    sys.exit(main())
