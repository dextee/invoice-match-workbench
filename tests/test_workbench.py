import argparse
import concurrent.futures
import csv
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import matcher
from workbench import ROOT, process


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.inputs = root / "inputs"
        self.ws = root / "state"
        shutil.copytree(ROOT / "examples" / "demo", self.inputs)

    def run_workbench(self, command="import", **kwargs):
        return process(argparse.Namespace(command=command, input=self.inputs, workspace=self.ws, **kwargs))

    def review(self, invoice, decision, reason="Supplier evidence reviewed"):
        return self.run_workbench("review", invoice=invoice, decision=decision, reviewer="Sample reviewer", reason=reason)

    def edit(self, name, change):
        path = self.inputs / (name+".csv")
        with path.open(encoding="utf-8", newline="") as source:
            data = [row for row in csv.reader(source) if row]
        change(data)
        with path.open("w", encoding="utf-8", newline="") as output:
            csv.writer(output).writerows(data)

    def count(self, table):
        with closing(sqlite3.connect(self.ws / "state.sqlite3")) as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_fixture_totals_trace_and_draft(self):
        batch, data = matcher.match(self.inputs)
        groups = data["invoices"]
        self.assertEqual(sum(row["total"] for row in groups.values()), 58700)
        line = groups["INV-1002"]["lines"][0]
        self.assertEqual((line["billed_across_batch"], line["received"]), (12, 10))
        self.assertEqual([row["source"] for row in line["receipts"]], ["receipts.csv row 4", "receipts.csv row 5"])
        self.assertEqual([row["received_qty"] for row in line["receipts"]], [6, 4])
        self.assertEqual(line["invoice_line"]["source"], "invoices.csv row 4")
        self.assertEqual(line["order"]["source"], "orders.csv row 4")
        self.assertEqual([matcher.issues(g) for g in groups.values()], [set(), {"short_receipt"}, {"price"}, {"missing_receipt"}])
        output, draft = self.run_workbench()
        self.assertEqual(output.name, batch)
        self.assertEqual(draft["total_minor"], 21500)
        self.assertEqual(len(draft["invoices"]), 1)
        self.assertEqual([line["line_total_minor"] for line in draft["invoices"][0]["lines"]], [20000, 1500])
        html = (output/"report.html").read_text("utf-8")
        self.assertIn("S$372.00", html)
        self.assertIn("12 billed across batch; 10 received", html)

    def test_reimport_export_and_parallel_delivery(self):
        output, _ = self.run_workbench()
        original = ((output/"report.html").read_bytes(), (output/"draft.json").read_bytes())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual([result[1]["total_minor"] for result in pool.map(self.run_workbench, ["import","export","import","export"])], [21500]*4)
        self.assertEqual(self.count("batches"), 1)
        self.assertEqual(original, ((output/"report.html").read_bytes(), (output/"draft.json").read_bytes()))

    def test_review_accept_defer_reject_reopen_and_repeat(self):
        self.review("INV-1002", "defer", "Await the remaining two units")
        self.review("INV-1004", "reject", "Receipt unavailable")
        output, draft = self.review("INV-1003", "accept-price", "Documented sample variance accepted")
        self.assertEqual(draft["total_minor"], 27500)
        self.assertEqual(draft["invoices"][-1]["review"]["reviewer"], "Sample reviewer")
        before = (output/"draft.json").read_bytes()
        self.review("INV-1003", "accept-price", "Documented sample variance accepted")
        self.run_workbench("export")
        self.assertEqual(self.count("decisions"), 3)
        self.assertEqual(before, (output/"draft.json").read_bytes())
        html = (output/"report.html").read_text("utf-8")
        self.assertIn("Deferred", html)
        self.assertIn("Rejected", html)
        self.assertEqual(self.review("INV-1003", "reopen")[1]["total_minor"], 21500)
        self.assertEqual(self.count("decisions"), 4)

    def test_nonprice_exceptions_cannot_be_overridden(self):
        for invoice in ("INV-1001", "INV-1002", "INV-1004"):
            with self.subTest(invoice=invoice), self.assertRaisesRegex(ValueError, "only of unit price"):
                self.review(invoice, "accept-price")
        with self.assertRaisesRegex(ValueError, "unknown invoice"):
            self.review("UNKNOWN", "defer")
        with self.assertRaisesRegex(ValueError, "requires a reviewer"):
            self.review("INV-1002", "defer", "   ")
        self.assertEqual(self.count("decisions"), 0)

    def test_changed_inputs_do_not_inherit_approval(self):
        old, _ = self.review("INV-1003", "accept-price")
        self.edit("receipts", lambda rows: rows.append(["GR-15/1", "PO-52/1", "2"]))
        new, draft = self.run_workbench()
        self.assertNotEqual(old, new)
        self.assertEqual([record["invoice"] for record in draft["invoices"]], ["INV-1001", "INV-1002"])
        self.assertEqual(draft["total_minor"], 43700)
        self.assertEqual(json.loads((old/"draft.json").read_text("utf-8"))["total_minor"], 27500)

    def test_excess_across_distinct_invoices_holds_each_one(self):
        self.edit("invoices", lambda rows: rows.append(["INV-1005", "1", "PO-51/1", "Paper Harbour Demo", "Archive box", "1", "25.00", "25.00"]))
        _, data = matcher.match(self.inputs)
        for key in ("INV-1001", "INV-1005"):
            self.assertEqual(matcher.issues(data["invoices"][key]), {"over_order", "short_receipt"})
        self.assertEqual(self.run_workbench()[1]["total_minor"], 0)

    def test_identity_arithmetic_missing_order(self):
        def change(rows):
            rows[1][4] = "Mismatched item"
            rows[2][-1] = "15.01"
            rows[-1][2] = "PO-99/9"
        self.edit("invoices", change)
        _, data = matcher.match(self.inputs)
        self.assertEqual(matcher.issues(data["invoices"]["INV-1001"]), {"identity", "arithmetic"})
        self.assertEqual(matcher.issues(data["invoices"]["INV-1004"]), {"missing_order"})

    def test_invalid_inputs_fail_before_state_creation(self):
        cases = [
            ("orders", lambda rows: rows.append(rows[1]), "duplicate"),
            ("invoices", lambda rows: rows.append(rows[1]), "duplicate"),
            ("receipts", lambda rows: rows[1].__setitem__(1, "unknown"), "unknown order"),
            ("invoices", lambda rows: rows[2].__setitem__(3, "different supplier"), "single supplier"),
            ("invoices", lambda rows: rows[1].__setitem__(-1, "0.001"), "two decimal"),
            ("receipts", lambda rows: rows[1].__setitem__(-1, "-1"), "positive whole"),
            ("orders", lambda rows: rows[0].pop(), "expected header"),
            ("invoices", lambda rows: rows[1].append("extra"), "extra field"),
            ("invoices", lambda rows: rows.__delitem__(slice(1,None)), "at least one"),
        ]
        for table, change, message in cases:
            with self.subTest(table=table, error=message):
                path = self.inputs / (table+".csv")
                saved = path.read_bytes()
                self.edit(table, change)
                with self.assertRaisesRegex(ValueError, message):
                    self.run_workbench()
                self.assertFalse(self.ws.exists())
                path.write_bytes(saved)

    def test_escape_text_in_report(self):
        self.edit("invoices", lambda rows: rows[1].__setitem__(4, "<script>alert(1)</script>"))
        output, _ = self.run_workbench()
        self.review("INV-1002", "defer", "<img src=x>")
        html = (output/"report.html").read_text("utf-8")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)


if __name__ == "__main__":
    unittest.main()
