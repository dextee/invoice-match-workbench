"""Render a portable HTML report without script or network requests."""

from html import escape
from matcher import money, status


def esc(value):
    return escape(str(value), quote=True)


CSS = """
*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:#f5f5ef;color:#192d2b;font:16px/1.5 Inter,'Segoe UI',sans-serif}main,nav>div{width:min(1140px,calc(100% - 48px));margin:auto}nav{background:#143c3b;color:white}nav>div{display:flex;justify-content:space-between;align-items:center;min-height:56px;font-weight:650}nav small{color:#d2e4dd;font-weight:500}.mast{display:flex;justify-content:space-between;align-items:flex-end;margin:38px 0 24px;gap:18px}.mast p{margin:4px 0 0;color:#4a605d}.mast h1{font:550 clamp(38px,5vw,64px)/1.03 Georgia,serif;letter-spacing:-.045em;margin:0}.mast code{font-size:13px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:22px 0 30px}.stat,.card{background:#fff;border:1px solid #c7d3ca;border-radius:10px}.stat{padding:17px 20px}.stat strong{display:block;font:550 34px/1.25 Georgia,serif}.stat small{color:#4a605d}.count{display:block;font-size:14px;color:#4a605d;margin-top:2px}.grid{display:grid;grid-template-columns:1fr 1.1fr;gap:20px;align-items:start}.card{overflow:hidden}.card h2{margin:0;padding:18px 20px;font-size:19px;letter-spacing:-.025em;border-bottom:1px solid #d9e1db}.row{display:flex;align-items:center;gap:14px;padding:14px 18px;border-bottom:1px solid #e1e6e1}.row:last-child{border:0}.row a{color:#123b3b;font-weight:670}.who{flex:1}.who small{display:block;color:#51645e}.currency{font-variant-numeric:tabular-nums;font-weight:600}.tag{display:inline-block;border-radius:5px;padding:2px 8px;font-size:12px;font-weight:650;background:#e6eee9;color:#284d44}.held,.deferred{background:#f5e3c6;color:#694614}.rejected{background:#f6dbd5;color:#812c22}.evidence{padding:18px 20px}.evidence h3{font-size:22px;margin:0 0 4px}.evidence p{margin:5px 0;color:#49605b}.compare{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0}.compare>div{padding:10px 12px;background:#f0f4ef;border-radius:6px}.compare b{display:block;font-size:23px}.compare small{color:#4a605d}.issue{background:#fcf0db;color:#5e431b;border-left:3px solid #c68b36;padding:7px 10px;margin:9px 0}.detail{margin:26px 0}.detail>h2{font-family:Georgia,serif;font-size:30px}.detail article{padding:17px 20px;margin:12px 0}.detail h3,.detail h4{margin:0 0 8px}.detail h4{margin-top:12px}.source{font:13px/1.6 Consolas,monospace;overflow-wrap:anywhere}.trace{margin:6px 0 0;padding-left:20px}.review{margin:10px 0 0;color:#2b5147}footer{padding:8px 0 30px;color:#4d625c}code{background:#e7ede7;border-radius:4px;padding:3px 5px;color:#334f47}@media(max-width:800px){.grid{grid-template-columns:1fr}.mast{align-items:start;flex-direction:column}.stats{gap:8px}.stat{padding:12px}.stat strong{font-size:24px}}@media(max-width:520px){main,nav>div{width:calc(100% - 24px)}.stats{grid-template-columns:1fr}.stat{display:flex;justify-content:space-between;align-items:center;gap:8px}.stat strong{font-size:21px}.row{gap:7px;padding:12px 10px}.mast{margin:25px 0 16px}.compare>div{padding:7px}.who small{font-size:12px}}
"""


def line_evidence(line):
    row = line["invoice_line"]
    order = line["order"]
    details = [f"Invoice {esc(row['invoice'])}, line {esc(row['line'])} · {esc(row['source'])} · {row['billed_qty']} × {money(row['unit_price_sgd'])}"]
    if order:
        details.append(f"Order {esc(order['order_line'])} · {esc(order['source'])} · {order['ordered_qty']} × {money(order['unit_price_sgd'])}")
    for record in line["receipts"]:
        details.append(f"Receipt {esc(record['receipt_line'])} · {esc(record['source'])} · {record['received_qty']} received")
    flags = "".join(f'<div class="issue">{esc(reason)}</div>' for reason in line["issues"].values())
    return flags + '<ul class="trace source">' + "".join(f"<li>{entry}</li>" for entry in details) + "</ul>"


def render(batch, data, reviews):
    groups = data["invoices"]
    states = {key: status(value, reviews.get(key)) for key, value in groups.items()}
    total = sum(g["total"] for g in groups.values())
    ready = [key for key, state in states.items() if state == "ready"]
    ready_amount = sum(groups[key]["total"] for key in ready)

    def stat(title, amount, count):
        return f'<div class="stat"><small>{title}</small><strong>{money(amount)}</strong><span class="count">{count} invoice{"s" if count != 1 else ""}</span></div>'

    stats = (stat("Batch total", total, len(groups)) + stat("Ready for draft", ready_amount, len(ready))
             + stat("Outside draft", total-ready_amount, len(groups)-len(ready)))
    rows, details = [], []
    for index, (key, group) in enumerate(groups.items(), 1):
        state = states[key]
        tag = f'<span class="tag {state}">{state.title()}</span>'
        rows.append(f'<div class="row"><div class="who"><a href="#invoice-{index}">{esc(key)}</a><small>{esc(group["supplier"])}</small></div>{tag}<span class="currency">{money(group["total"])}</span></div>')
        review = reviews.get(key)
        note = "" if not review else f'<p class="review">Review · {esc(review["action"])} · {esc(review["reviewer"])} · {esc(review["reason"])}</p>'
        evidence = "".join(f'<h4>Line {esc(line["invoice_line"]["line"])} · {esc(line["invoice_line"]["item"])}</h4>{line_evidence(line)}' for line in group["lines"])
        details.append(f'<article id="invoice-{index}" class="card"><h3>{esc(key)} · {money(group["total"])} {tag}</h3>{evidence}{note}</article>')
    candidates = [(key,line) for key,group in groups.items() for line in group["lines"]]
    key, line = next(((key,line) for key,line in candidates if line["issues"]), candidates[0])
    ordered = line["order"]["ordered_qty"] if line["order"] else "—"
    compare = "".join(f'<div><small>{label}</small><b>{value}</b></div>' for label,value in (
        ("Ordered", ordered), ("Received", line["received"]), ("Billed in batch", line["billed_across_batch"])))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Invoice Match Workbench · Batch review</title><style>{CSS}</style></head>
<body><nav><div><span>Invoice Match Workbench</span><small>Purchase operations</small></div></nav><main>
<header class="mast"><div><h1>Invoice review</h1><p>Purchase orders, received goods and submitted invoices in one batch.</p></div><code>Batch {esc(batch[:12])}</code></header>
<section class="stats" aria-label="Batch totals">{stats}</section>
<div class="grid"><section class="card"><h2>Invoice queue</h2>{''.join(rows)}</section>
<section class="card"><h2>Line evidence</h2><div class="evidence"><h3>{esc(key)} · {esc(line['invoice_line']['item'])}</h3><p>{esc(line['invoice_line']['supplier'])} · {esc(line['invoice_line']['order_line'])}</p><div class="compare">{compare}</div>{line_evidence(line)}</div></section></div>
<section class="detail"><h2>Invoice details and decisions</h2>{''.join(details)}</section><footer>All amounts in SGD. Draft eligibility applies to this input snapshot.</footer></main></body></html>"""
