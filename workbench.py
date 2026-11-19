"""Import three CSV files, review invoice exceptions and regenerate a local draft."""

import argparse
import csv
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from matcher import issues, match, money, status
from report import render

ROOT = Path(__file__).resolve().parent


def open_workspace(folder):
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "state.sqlite3", timeout=20)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS decisions (
            seq INTEGER PRIMARY KEY, batch TEXT NOT NULL, invoice TEXT NOT NULL,
            action TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS decision_lookup ON decisions(batch, invoice, seq);
    """)
    return db


def latest(db, batch):
    return {invoice: {"action": action, "reviewer": who, "reason": why, "at": at}
            for invoice, action, who, why, at in db.execute(
                "SELECT invoice, action, reviewer, reason, at FROM decisions WHERE batch=? ORDER BY seq", (batch,))}


def record(db, batch, data, previous, invoice, action, who, why):
    who, why = who.strip(), why.strip()
    if not who or not why:
        raise ValueError("review requires a reviewer name and a reason")
    if invoice not in data["invoices"]:
        raise ValueError(f"unknown invoice: {invoice}")
    flags = issues(data["invoices"][invoice])
    if action == "accept-price" and flags != {"price"}:
        raise ValueError("accept-price requires exceptions consisting only of unit price differences")
    if action == "reopen" and invoice not in previous:
        raise ValueError("reopen requires an existing reviewer decision")
    old = previous.get(invoice, {})
    if (old.get("action"), old.get("reviewer"), old.get("reason")) == (action, who, why):
        return
    db.execute("INSERT INTO decisions(batch,invoice,action,reviewer,reason,at) VALUES(?,?,?,?,?,?)",
               (batch, invoice, action, who, why, datetime.now(timezone.utc).isoformat(timespec="microseconds")))


def replace_file(destination, contents):
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        path = Path(temporary.name)
    try:
        path.write_bytes(contents.encode("utf-8"))
        os.replace(path, destination)
    finally:
        path.unlink(missing_ok=True)


def make_draft(batch, data, reviews):
    entries = []
    for key, invoice in data["invoices"].items():
        review = reviews.get(key)
        if status(invoice, review) == "ready":
            lines = []
            for line in invoice["lines"]:
                item = line["invoice_line"]
                lines.append({"line": item["line"], "order_line": item["order_line"], "item": item["item"],
                              "quantity": item["billed_qty"], "unit_price_minor": item["unit_price_sgd"],
                              "line_total_minor": item["line_total_sgd"], "source": item["source"]})
            entries.append({"invoice": key, "supplier": invoice["supplier"], "total_minor": invoice["total"],
                            "lines": lines, "review": review})
    return {"format": 1, "purpose": "review draft", "batch": batch, "currency": "SGD",
            "source_files_sha256": data["manifest"]["files"], "money_unit": "cents",
            "total_minor": sum(entry["total_minor"] for entry in entries), "invoices": entries}


def process(args):
    batch, data = match(args.input)
    workspace = args.workspace.resolve()
    db = open_workspace(workspace)
    try:
        # Recording, review and export are serialized across callers in this workspace.
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO batches VALUES(?,?)", (batch, json.dumps(data)))
            previous = latest(db, batch)
            if args.command == "review":
                record(db, batch, data, previous, args.invoice, args.decision, args.reviewer, args.reason)
        # Export the committed state. If writing is interrupted, rerun to regenerate both files.
        with db:
            db.execute("BEGIN IMMEDIATE")
            reviews = latest(db, batch)
            draft = make_draft(batch, data, reviews)
            output = workspace / batch
            output.mkdir(exist_ok=True)
            replace_file(output / "draft.json", json.dumps(draft, indent=2, ensure_ascii=False) + "\n")
            replace_file(output / "report.html", render(batch, data, reviews))
        return output, draft
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo", "import", "review", "export"])
    parser.add_argument("--input", type=Path, default=ROOT / "examples" / "demo", help="folder containing three CSV files")
    parser.add_argument("--workspace", type=Path, default=Path(".runs"), help="local state and output directory")
    parser.add_argument("--invoice")
    parser.add_argument("--decision", choices=["defer", "reject", "accept-price", "reopen"])
    parser.add_argument("--reviewer")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    review_fields = (args.invoice, args.decision, args.reviewer, args.reason)
    if args.command == "review" and not all(review_fields):
        parser.error("review requires --invoice, --decision, --reviewer and --reason")
    if args.command != "review" and any(item is not None for item in review_fields):
        parser.error("review flags require the review command")
    try:
        output, draft = process(args)
    except (ValueError, OSError, sqlite3.Error, csv.Error) as exc:
        parser.exit(1, f"Input or workspace error: {exc}\n")
    print(f"Draft: {len(draft['invoices'])} invoice(s), {money(draft['total_minor'])}\nReport: {output / 'report.html'}\nDraft file: {output / 'draft.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
