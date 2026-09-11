#!/usr/bin/env python3
# pylint: disable=C
"""Archive closed CfD noms.

Active noms tracked in data/cfds.json.
Noms are archived when {{cfdh}} and {{cfdf}} are the first and last top-level templates.

"""

from __future__ import annotations
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
os.environ.setdefault("PYWIKIBOT_DIR", str(SCRIPT_DIR))  # keep before pywikibot import

import mwparserfromhell
import pywikibot

BASE = "Commons:Categories for discussion"
ARCHIVE_BASE = f"{BASE}/Archive"
FIRST_MONTH = (2015, 12)
DEFAULT_DATA_FILE = SCRIPT_DIR / "data" / "cfds.json"

LAST_TOUCHED = "last_touched"
MONTH_KEY_RE = re.compile(r"\A\d{4}/\d{2}\Z")
HEADER_TEMPLATE = "cfdh"
FOOTER_TEMPLATE = "cfdf"

logger = logging.getLogger("cfd-archive")


def normalize_title(value: str) -> str:
    return re.sub(r"[\s_]+", "_", value.strip())


def template_key(name: object) -> str:
    key = re.sub(r"[\s_]+", " ", str(name).strip()).lower()
    if key.startswith("template:"):
        key = key[len("template:") :].strip()
    return key


def month_key(year: int, month: int) -> str:
    return f"{year:04d}/{month:02d}"


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def months_between(start: tuple[int, int], end: tuple[int, int]):
    current = start
    while current <= end:
        yield current
        current = next_month(*current)


def subpage_title(ym: str, name: str) -> str:
    return f"{BASE}/{ym}/Category:{name}"


def transclusion(ym: str, name: str) -> str:
    return "{{" + subpage_title(ym, name) + "}}"


def cfd_subpage_name(template_name: str, ym: str) -> str | None:
    prefix = normalize_title(f"{BASE}/{ym}/Category:")
    name = normalize_title(template_name)
    if not name.lower().startswith(prefix.lower()):
        return None
    return name[len(prefix) :] or None


def listed_names(text: str, ym: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for template in mwparserfromhell.parse(text).filter_templates():
        name = cfd_subpage_name(str(template.name), ym)
        if name is not None and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def is_closed(text: str) -> bool:
    """True when {{cfdh}} and {{cfdf}} are the first and last top-level templates."""
    templates = mwparserfromhell.parse(text).filter_templates(recursive=False)
    if not templates:
        return False
    return (
        template_key(templates[0].name) == HEADER_TEMPLATE
        and template_key(templates[-1].name) == FOOTER_TEMPLATE
    )


def sole_transclusion(line: str, ym: str) -> str | None:
    """Return the CfD name if line holds nothing but that one transclusion."""
    stripped = line.strip()
    if not (stripped.startswith("{{") and stripped.endswith("}}")):
        return None
    templates = mwparserfromhell.parse(stripped).filter_templates(recursive=False)
    if len(templates) != 1 or str(templates[0]).strip() != stripped:
        return None
    return cfd_subpage_name(str(templates[0].name), ym)


def strip_transclusions(text: str, ym: str, names: list[str]) -> tuple[str, set[str]]:
    """Remove the transclusions for *names* from *text*."""
    wanted = {normalize_title(name) for name in names}
    removed: set[str] = set()

    kept_lines = []
    for line in text.split("\n"):
        name = sole_transclusion(line, ym)
        if name is not None and normalize_title(name) in wanted:
            removed.add(normalize_title(name))
            continue
        kept_lines.append(line)
    new_text = "\n".join(kept_lines)

    # Entries sharing a line with other content need node-level removal.
    if wanted - removed:
        wikicode = mwparserfromhell.parse(new_text)
        for template in wikicode.filter_templates():
            name = cfd_subpage_name(str(template.name), ym)
            if name is None:
                continue
            key = normalize_title(name)
            if key in wanted and key not in removed:
                wikicode.remove(template)
                removed.add(key)
        new_text = str(wikicode)

    return new_text, removed


def append_transclusions(text: str, ym: str, names: list[str]) -> tuple[str, list[str]]:
    """Append missing transclusions for *names*, returning the new text."""
    present = {normalize_title(name) for name in listed_names(text, ym)}
    added = [name for name in names if normalize_title(name) not in present]
    if not added:
        return text, []

    lines = [transclusion(ym, name) for name in added]
    body = text.rstrip("\n")
    new_text = "\n".join(([body] if body else []) + lines) + "\n"
    return new_text, added


# State file


def load_state(path: Path) -> tuple[dict[str, list[str]], int | None]:
    """Return the tracked months and the last_touched timestamp."""
    if not path.exists():
        logger.info(
            "%s does not exist; starting from %s", path, month_key(*FIRST_MONTH)
        )
        return {}, None

    # utf-8-sig so a hand-edited file with a BOM still loads.
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    cfds = {key: list(value) for key, value in data.items() if MONTH_KEY_RE.match(key)}

    last_touched = data.get(LAST_TOUCHED)
    if last_touched is not None:
        try:
            last_touched = int(last_touched)
        except (TypeError, ValueError):
            logger.warning("Ignoring unusable %s value %r", LAST_TOUCHED, last_touched)
            last_touched = None

    return cfds, last_touched


def write_state(path: Path, cfds: dict[str, list[str]], timestamp: int) -> None:
    payload: dict[str, object] = {LAST_TOUCHED: timestamp}
    for key in sorted(cfds, reverse=True):
        payload[key] = cfds[key]

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)
    logger.info("Wrote %s (%d months tracked)", path, len(cfds))


def ensure_month_keys(
    cfds: dict[str, list[str]], last_touched: int | None, now: datetime
) -> list[str]:
    """Add empty keys for every month not yet tracked and return the new keys."""
    current = (now.year, now.month)
    if last_touched is None:
        start = FIRST_MONTH
    else:
        previous_run = datetime.fromtimestamp(last_touched, tz=timezone.utc)
        start = next_month(previous_run.year, previous_run.month)

    added = []
    for ym in months_between(start, current):
        key = month_key(*ym)
        if key not in cfds:
            cfds[key] = []
            added.append(key)

    for ym in (current, previous_month(*current)):
        key = month_key(*ym)
        if ym >= FIRST_MONTH and key not in cfds:
            cfds[key] = []
            added.append(key)

    if added:
        logger.info(
            "Added %d month(s) to track: %s", len(added), ", ".join(sorted(added))
        )
    return added


# Wiki access


def preload(site, pages):
    """Fetch page text in batches of site.maxlimit instead of one request each."""
    pages = list(pages)
    if not pages:
        return {}

    groupsize = site.maxlimit
    total = len(pages)
    logger.info("Preloading %d page(s) in batches of %d", total, groupsize)

    loaded = {}
    progress_every = groupsize * 10
    for count, page in enumerate(
        site.preloadpages(pages, groupsize=groupsize, quiet=True), start=1
    ):
        loaded[normalize_title(page.title())] = page
        if total > progress_every and count % progress_every == 0:
            logger.info("  preloaded %d/%d", count, total)
    return loaded


def page_text(page) -> str | None:
    """Return page text, or None when the page does not exist."""
    return page.text if page is not None and page.exists() else None


def can_login_unattended(site) -> bool:
    """True when logging in will not block on an interactive password prompt."""
    if site.username() is None:
        return False
    if pywikibot.config.password_file or pywikibot.config.authenticate:
        return True
    return sys.stdin is not None and sys.stdin.isatty()


def login(site, *, required: bool) -> bool:
    if site.logged_in():
        logger.info("Logged in as %s", site.username())
        return True

    if not can_login_unattended(site):
        message = (
            "No usable credentials: set a username in user-config.py and a "
            "password_file (see user-config.py comments)"
        )
        if required:
            raise SystemExit(f"ERROR: {message}")
        logger.warning("%s; continuing anonymously", message)
        return False

    try:
        site.login()
    except Exception as error:  # pylint: disable=W0718 - login failures vary by setup
        if required:
            raise SystemExit(f"ERROR: login failed: {error}") from error
        logger.warning("Login failed (%s); continuing anonymously", error)
        return False

    logger.info("Logged in as %s", site.username())
    return True


def save(page: pywikibot.Page, summary: str, *, live: bool) -> None:
    if not live:
        logger.info("[dry run] would save %s: %s", page.title(), summary)
        return
    page.save(summary=summary, minor=False, bot=True)
    logger.info("Saved %s: %s", page.title(), summary)


def archive_month(site, ym: str, names: list[str], *, live: bool) -> None:
    """Append *names* to the archive page and remove them from the month page.

    Archive first, then remove, so nothing is dropped if the second edit
    fails. Both edits are idempotent, so the caller can safely retry.
    """
    archive_page = pywikibot.Page(site, f"{ARCHIVE_BASE}/{ym}")
    month_page = pywikibot.Page(site, f"{BASE}/{ym}")
    pages = preload(site, [archive_page, month_page])
    archive_page = pages.get(normalize_title(archive_page.title()), archive_page)
    month_page = pages.get(normalize_title(month_page.title()), month_page)

    archive_text = page_text(archive_page) or ""
    new_archive_text, added = append_transclusions(archive_text, ym, names)
    if added:
        archive_page.text = new_archive_text
        save(
            archive_page,
            f"[[Commons:Bots/Requests/MSKbot|[BOT]]] archiving {len(added)} closed [[COM:CFD|CfD]] discussion{'s' if len(added) > 1 else ''} "
            f"from [[{BASE}/{ym}|{ym}]]",
            live=live,
        )
    else:
        logger.info(
            "%s already lists all %d discussion(s)", archive_page.title(), len(names)
        )

    month_text = page_text(month_page)
    if month_text is None:
        logger.warning("%s does not exist; nothing to remove", month_page.title())
        return

    new_month_text, removed = strip_transclusions(month_text, ym, names)
    missing = {normalize_title(name) for name in names} - removed
    if missing:
        logger.warning(
            "%d closed discussion(s) were not listed on %s: %s",
            len(missing),
            month_page.title(),
            ", ".join(sorted(missing)),
        )
    if removed:
        month_page.text = new_month_text
        save(
            month_page,
            f"[[Commons:Bots/Requests/MSKbot|[BOT]]] archiving {len(removed)} closed [[COM:CFD|CfD]] discussion{'s' if len(removed) > 1 else ''} "
            f" to [[{ARCHIVE_BASE}/{ym}|Archive/{ym}]]",
            live=live,
        )


# Pipeline


def months_to_scan(
    cfds: dict[str, list[str]], added: list[str], now: datetime
) -> list[str]:
    """Newly tracked months plus the current and previous month."""
    targets = set(added)
    for ym in ((now.year, now.month), previous_month(now.year, now.month)):
        key = month_key(*ym)
        if key in cfds:
            targets.add(key)
    return sorted(targets)


def scan_month_pages(site, cfds: dict[str, list[str]], keys: list[str]) -> int:
    """Merge transclusions listed on the given month pages into *cfds*."""
    pages = preload(site, [pywikibot.Page(site, f"{BASE}/{key}") for key in keys])

    total_new = 0
    for key in keys:
        page = pages.get(normalize_title(f"{BASE}/{key}"))
        text = page_text(page)
        if text is None:
            logger.info("%s/%s does not exist", BASE, key)
            continue

        known = {normalize_title(name) for name in cfds[key]}
        new = [
            name
            for name in listed_names(text, key)
            if normalize_title(name) not in known
        ]
        if new:
            cfds[key].extend(new)
            total_new += len(new)
        logger.info("%s: %d listed, %d new", key, len(cfds[key]), len(new))

    return total_new


def find_closed(site, cfds: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return the closed discussions, removing them from *cfds*."""
    index: dict[str, tuple[str, str]] = {}
    pages = []
    for ym in sorted(cfds):
        for name in cfds[ym]:
            title = subpage_title(ym, name)
            index[normalize_title(title)] = (ym, name)
            pages.append(pywikibot.Page(site, title))

    if not pages:
        return {}

    loaded = preload(site, pages)
    cfds_todo: dict[str, list[str]] = {}
    missing = 0
    for key, (ym, name) in index.items():
        page = loaded.get(key)
        text = page_text(page)
        if text is None:
            missing += 1
            continue
        if is_closed(text):
            cfds_todo.setdefault(ym, []).append(name)
            logger.debug("closed: %s", subpage_title(ym, name))

    for ym, names in cfds_todo.items():
        closed = set(names)
        cfds[ym] = [name for name in cfds[ym] if name not in closed]

    if missing:
        logger.info(
            "%d tracked discussion(s) have no page; leaving them tracked", missing
        )
    return cfds_todo


def run(args: argparse.Namespace) -> int:
    started = int(time.time())
    now = datetime.now(timezone.utc)

    cfds, last_touched = load_state(args.data)
    added = ensure_month_keys(cfds, last_touched, now)

    site = pywikibot.Site("commons", "commons")
    login(site, required=args.live)

    scan_keys = months_to_scan(cfds, added, now)
    logger.info("Scanning %d month page(s)", len(scan_keys))
    scan_month_pages(site, cfds, scan_keys)

    tracked = sum(len(names) for names in cfds.values())
    logger.info("Checking %d tracked discussion(s) for closure", tracked)
    cfds_todo = find_closed(site, cfds)

    closed = sum(len(names) for names in cfds_todo.values())
    if not closed:
        logger.info("Nothing to archive")
    else:
        logger.info(
            "Archiving %d closed discussion(s) across %d month(s)",
            closed,
            len(cfds_todo),
        )

    failed = 0
    for ym in sorted(cfds_todo):
        names = cfds_todo[ym]
        try:
            archive_month(site, ym, names, live=args.live)
        except Exception as error:  # noqa: BLE001 - keep other months going
            failed += 1
            logger.error("Failed to archive %s (%s); will retry next run", ym, error)
            # Put the entries back so the next run picks them up again.
            cfds[ym] = names + cfds.get(ym, [])

    if args.live:
        write_state(args.data, cfds, started)
    else:
        logger.info("[dry run] not writing %s", args.data)

    remaining = sum(len(names) for names in cfds.values())
    logger.info("Done: %d closed, %d still open", closed, remaining)
    return 1 if failed else 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive closed Commons Categories for discussion entries."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="save wiki edits and cfds.json (default: dry run)",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_FILE,
        help=f"state file path (default: {DEFAULT_DATA_FILE})",
    )
    parser.add_argument("--verbose", action="store_true", help="log debug output")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )
    logging.Formatter.converter = time.gmtime
    if not args.live:
        logger.info("This is a dry run; pass --live to edit")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
