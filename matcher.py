"""Three-way matching for exported, single-currency line records."""

import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

COLUMNS = {
    "orders.csv": "order_line supplier item ordered_qty unit_price_sgd".split(),
    "receipts.csv": "receipt_line order_line received_qty".split(),
    "invoices.csv": "invoice line order_line supplier item billed_qty unit_price_sgd line_total_sgd".split(),
}


def money(cents):
    return f"S${cents // 100:,}.{cents % 100:02d}"


def read_table(data, name):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    if reader.fieldnames != COLUMNS[name]:
        raise ValueError(f"{name}: expected header {','.join(COLUMNS[name])}")
    result = []
    for row in reader:
        source = f"{name} row {reader.line_num}"
        if None in row or any(not value or not value.strip() for value in row.values()):
            raise ValueError(f"{source}: missing or extra field")
        row = {key: value.strip() for key, value in row.items()}
        for key, value in row.items():
            if key.endswith("_qty"):
                if not re.fullmatch(r"[1-9][0-9]{0,8}", value):
                    raise ValueError(f"{source}: {key} must be a positive whole quantity")
                row[key] = int(value)
            if key.endswith("_sgd"):
                if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", value):
                    raise ValueError(f"{source}: {key} must be a nonnegative SGD amount with at most two decimal places")
                whole, _, fraction = value.partition(".")
                row[key] = int(whole) * 100 + int(fraction.ljust(2, "0"))
        row["source"] = source
        result.append(row)
    return result


def unique_rows(rows, keys):
    found = {}
    for row in rows:
        identity = tuple(row[key] for key in keys)
        if identity in found:
            raise ValueError(f"{row['source']}: duplicate {', '.join(keys)} {identity}")
        found[identity] = row
    return found


def match(folder):
    hashes = {}
    tables = {}
    for name in COLUMNS:
        contents = (Path(folder) / name).read_bytes()
        hashes[name] = hashlib.sha256(contents).hexdigest()
        tables[name] = read_table(contents, name)
    # Include the matching rules version when identifying an immutable snapshot.
    manifest = {"rules": 1, "files": hashes}
    batch = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    orders = {key[0]: row for key, row in unique_rows(tables["orders.csv"], ["order_line"]).items()}
    receipts = defaultdict(list)
    for row in unique_rows(tables["receipts.csv"], ["receipt_line"]).values():
        if row["order_line"] not in orders:
            raise ValueError(f"{row['source']}: unknown order line {row['order_line']}")
        receipts[row["order_line"]].append(row)
    invoices = tables["invoices.csv"]
    if not invoices:
        raise ValueError("invoices.csv: expected at least one invoice line")
    unique_rows(invoices, ["invoice", "line"])
    total_billed = Counter()
    suppliers_by_invoice = defaultdict(set)
    for row in invoices:
        total_billed[row["order_line"]] += row["billed_qty"]
        suppliers_by_invoice[row["invoice"]].add(row["supplier"])
    if any(len(values) > 1 for values in suppliers_by_invoice.values()):
        raise ValueError("invoice IDs must identify a single supplier within a batch")
    groups = {}
    for row in invoices:
        order_id = row["order_line"]
        order = orders.get(order_id)
        deliveries = receipts[order_id]
        received = sum(r["received_qty"] for r in deliveries)
        billed = total_billed[order_id]
        flags = {}

        if not order:
            flags["missing_order"] = "The referenced order line is absent."
        else:
            if order["supplier"] != row["supplier"] or order["item"] != row["item"]:
                flags["identity"] = "Supplier or item differs from the purchase order."
            if order["unit_price_sgd"] != row["unit_price_sgd"]:
                flags["price"] = f"Invoice rate {money(row['unit_price_sgd'])}; order rate {money(order['unit_price_sgd'])}."
            if billed > order["ordered_qty"]:
                flags["over_order"] = f"{billed} billed across batch; {order['ordered_qty']} ordered."
            if not deliveries:
                flags["missing_receipt"] = "No goods receipt for this order line."
            elif billed > received:
                flags["short_receipt"] = f"{billed} billed across batch; {received} received."
        if row["billed_qty"] * row["unit_price_sgd"] != row["line_total_sgd"]:
            flags["arithmetic"] = "Submitted line total differs from quantity times rate."
        line = {"invoice_line": row, "order": order, "receipts": deliveries,
                "received": received, "billed_across_batch": billed, "issues": flags}
        group = groups.setdefault(row["invoice"], {"supplier": row["supplier"], "total": 0, "lines": []})
        group["total"] += row["line_total_sgd"]
        group["lines"].append(line)
    return batch, {"manifest": manifest, "invoices": groups}


def issues(invoice):
    return {code for line in invoice["lines"] for code in line["issues"]}


def status(invoice, review):
    if review and review["action"] != "reopen":
        return {"defer": "deferred", "reject": "rejected", "accept-price": "ready"}[review["action"]]
    return "held" if issues(invoice) else "ready"
