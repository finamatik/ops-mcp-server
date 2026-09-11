"""Data layer for the Ops MCP server, SQLite here, swappable for any REST/ERP/CRM client.

Everything the MCP tools can do goes through this class, so the server file stays thin and the
same tool contract can sit on top of Procore, HubSpot, SAP B1, Odoo or a plain Postgres.
"""
import json
import os
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("OPS_MCP_DB", Path(os.environ.get("OPS_MCP_HOME", Path.home() / ".finamatik-ops-mcp")) / "ops.db"))
STAGES = ["new", "qualified", "proposal", "negotiation", "won", "lost"]

SCHEMA = """
CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT, company TEXT, email TEXT, phone TEXT, city TEXT, owner TEXT, tags TEXT, created_at TEXT);
CREATE TABLE deals (id TEXT PRIMARY KEY, contact_id TEXT, title TEXT, stage TEXT, value_aed REAL, expected_close TEXT, updated_at TEXT);
CREATE TABLE tasks (id TEXT PRIMARY KEY, contact_id TEXT, title TEXT, due_date TEXT, assignee TEXT, status TEXT, created_at TEXT, completed_at TEXT);
CREATE TABLE notes (id TEXT PRIMARY KEY, contact_id TEXT, text TEXT, author TEXT, created_at TEXT);
CREATE TABLE inventory (sku TEXT PRIMARY KEY, name TEXT, qty INTEGER, reorder_level INTEGER, unit_price_aed REAL, supplier TEXT);
CREATE TABLE orders (id TEXT PRIMARY KEY, contact_id TEXT, sku TEXT, qty INTEGER, total_aed REAL, status TEXT, created_at TEXT);
"""


class OpsBackend:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        if fresh:
            self.con.executescript(SCHEMA)
            seed(self.con)

    # ---- helpers ----
    def _rows(self, sql, args=()):
        return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        r = self.con.execute(sql, args).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _now():
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    # ---- contacts ----
    def search_contacts(self, query, limit=10):
        q = f"%{query.strip()}%"
        return self._rows("SELECT id,name,company,email,phone,city,owner,tags FROM contacts WHERE name LIKE ? OR company LIKE ? OR email LIKE ? OR phone LIKE ? OR city LIKE ? ORDER BY name LIMIT ?", (q, q, q, q, q, limit))

    def get_contact(self, contact_id):
        c = self._one("SELECT * FROM contacts WHERE id=?", (contact_id,))
        if not c:
            return None
        c["deals"] = self._rows("SELECT id,title,stage,value_aed,expected_close FROM deals WHERE contact_id=? ORDER BY updated_at DESC", (contact_id,))
        c["open_tasks"] = self._rows("SELECT id,title,due_date,assignee FROM tasks WHERE contact_id=? AND status='open' ORDER BY due_date", (contact_id,))
        c["recent_notes"] = self._rows("SELECT text,author,created_at FROM notes WHERE contact_id=? ORDER BY created_at DESC LIMIT 5", (contact_id,))
        c["orders"] = self._rows("SELECT id,sku,qty,total_aed,status,created_at FROM orders WHERE contact_id=? ORDER BY created_at DESC LIMIT 5", (contact_id,))
        return c

    def add_note(self, contact_id, text, author="claude-mcp"):
        if not self._one("SELECT id FROM contacts WHERE id=?", (contact_id,)):
            raise ValueError(f"contact {contact_id} not found")
        nid = f"N-{random.randint(10000, 99999)}"
        self.con.execute("INSERT INTO notes VALUES (?,?,?,?,?)", (nid, contact_id, text, author, self._now()))
        self.con.commit()
        return {"note_id": nid, "contact_id": contact_id}

    # ---- tasks ----
    def list_tasks(self, status="open", assignee=None, due_before=None, limit=25):
        sql, args = "SELECT t.*, c.name AS contact_name FROM tasks t LEFT JOIN contacts c ON c.id=t.contact_id WHERE 1=1", []
        if status != "all":
            sql += " AND t.status=?"; args.append(status)
        if assignee:
            sql += " AND t.assignee=?"; args.append(assignee)
        if due_before:
            sql += " AND t.due_date<=?"; args.append(due_before)
        sql += " ORDER BY t.due_date LIMIT ?"; args.append(limit)
        return self._rows(sql, args)

    def create_task(self, contact_id, title, due_date, assignee):
        if not self._one("SELECT id FROM contacts WHERE id=?", (contact_id,)):
            raise ValueError(f"contact {contact_id} not found")
        tid = f"T-{random.randint(10000, 99999)}"
        self.con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)", (tid, contact_id, title, due_date, assignee, "open", self._now(), None))
        self.con.commit()
        return self._one("SELECT * FROM tasks WHERE id=?", (tid,))

    def complete_task(self, task_id):
        t = self._one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not t:
            raise ValueError(f"task {task_id} not found")
        if t["status"] == "done":
            return {**t, "already_done": True}
        self.con.execute("UPDATE tasks SET status='done', completed_at=? WHERE id=?", (self._now(), task_id))
        self.con.commit()
        return self._one("SELECT * FROM tasks WHERE id=?", (task_id,))

    # ---- deals ----
    def pipeline_summary(self):
        rows = self._rows("SELECT stage, COUNT(*) AS deals, ROUND(SUM(value_aed),2) AS value_aed FROM deals GROUP BY stage")
        by = {r["stage"]: {"deals": r["deals"], "value_aed": r["value_aed"]} for r in rows}
        open_stages = [s for s in STAGES if s not in ("won", "lost")]
        return {"by_stage": {s: by.get(s, {"deals": 0, "value_aed": 0}) for s in STAGES},
                "open_deals": sum(by.get(s, {}).get("deals", 0) for s in open_stages),
                "open_value_aed": round(sum(by.get(s, {}).get("value_aed", 0) for s in open_stages), 2),
                "closing_this_month": self._rows("SELECT d.id,d.title,d.stage,d.value_aed,d.expected_close,c.name AS contact FROM deals d JOIN contacts c ON c.id=d.contact_id WHERE d.stage NOT IN ('won','lost') AND d.expected_close<=? ORDER BY d.expected_close", ((date.today() + timedelta(days=30)).isoformat(),))}

    def update_deal_stage(self, deal_id, stage):
        if stage not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}")
        d = self._one("SELECT * FROM deals WHERE id=?", (deal_id,))
        if not d:
            raise ValueError(f"deal {deal_id} not found")
        self.con.execute("UPDATE deals SET stage=?, updated_at=? WHERE id=?", (stage, self._now(), deal_id))
        self.con.commit()
        return {"deal_id": deal_id, "from": d["stage"], "to": stage}

    # ---- inventory / orders ----
    def inventory(self, sku=None, low_stock_only=False):
        if sku:
            r = self._one("SELECT * FROM inventory WHERE sku=?", (sku,))
            return [r] if r else []
        sql = "SELECT * FROM inventory" + (" WHERE qty<=reorder_level" if low_stock_only else "") + " ORDER BY sku"
        return self._rows(sql)

    def create_order(self, contact_id, sku, qty, dry_run=True):
        if qty <= 0:
            raise ValueError("qty must be a positive integer")
        if not self._one("SELECT id FROM contacts WHERE id=?", (contact_id,)):
            raise ValueError(f"contact {contact_id} not found")
        item = self._one("SELECT * FROM inventory WHERE sku=?", (sku,))
        if not item:
            raise ValueError(f"unknown SKU {sku}")
        if item["qty"] < qty:
            raise ValueError(f"insufficient stock for {sku}: {item['qty']} available, {qty} requested (supplier: {item['supplier']})")
        total = round(item["unit_price_aed"] * qty, 2)
        preview = {"contact_id": contact_id, "sku": sku, "item": item["name"], "qty": qty, "unit_price_aed": item["unit_price_aed"], "total_aed": total, "stock_after": item["qty"] - qty}
        if dry_run:
            return {"dry_run": True, "preview": preview, "next_step": "call again with dry_run=false to place the order"}
        oid = f"SO-{random.randint(10000, 99999)}"
        self.con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", (oid, contact_id, sku, qty, total, "confirmed", self._now()))
        self.con.execute("UPDATE inventory SET qty=qty-? WHERE sku=?", (qty, sku))
        self.con.commit()
        return {"dry_run": False, "order_id": oid, **preview}


def seed(con):
    """sample data only, names, companies and numbers are invented."""
    rnd = random.Random(42)
    first = ["Sara", "Bilal", "Omar", "Maya", "Ahmed", "Fatima", "Hassan", "Layla", "Yusuf", "Noor", "Tariq", "Amira", "Zain", "Hana", "Karim", "Dina", "Faisal", "Rania", "Ali", "Mariam"]
    last = ["Khan", "Ahmed", "Haddad", "Fernandes", "Malik", "Rahman", "Sheikh", "Qureshi", "Nasser", "Farouk", "Iqbal", "Saleh", "Butt", "Mansour", "Hussain"]
    companies = ["Khan Interiors", "Blue Palm Realty", "Marina Dental Clinic", "Gulf Fitouts LLC", "Nasser Trading", "Lahore Logistics", "Oasis Cafes", "JLT Serviced Offices", "Desert Rose Events", "Pearl Pharmacy Group", "Al Noor Schools", "Skyline Facilities"]
    cities = ["Dubai", "Sharjah", "Abu Dhabi", "Lahore", "Karachi", "Riyadh"]
    owners = ["hamza", "sana", "ali"]
    contacts = []
    for i in range(40):
        fn, ln = rnd.choice(first), rnd.choice(last)
        cid = f"C-{1001 + i}"
        contacts.append(cid)
        con.execute("INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?,?)", (cid, f"{fn} {ln}", rnd.choice(companies), f"{fn.lower()}.{ln.lower()}{i}@example.com",
                    f"+9715{rnd.randint(10000000, 99999999)}" if rnd.random() < 0.7 else f"+9230{rnd.randint(10000000, 99999999)}", rnd.choice(cities), rnd.choice(owners),
                    json.dumps(rnd.sample(["whatsapp-lead", "referral", "ads", "returning", "vip"], k=rnd.randint(0, 2))), (date.today() - timedelta(days=rnd.randint(1, 400))).isoformat()))
    titles = ["AC maintenance contract", "Office deep-clean programme", "Villa plumbing retrofit", "Quarterly HVAC servicing", "Fit-out snagging package", "Annual FM contract"]
    for i in range(25):
        stage = rnd.choices(STAGES, weights=[5, 6, 5, 3, 4, 2])[0]
        con.execute("INSERT INTO deals VALUES (?,?,?,?,?,?,?)", (f"D-{2001 + i}", rnd.choice(contacts), rnd.choice(titles), stage, rnd.choice([4500, 9800, 12000, 18500, 27000, 42000, 65000]),
                    (date.today() + timedelta(days=rnd.randint(-10, 90))).isoformat(), (datetime.utcnow() - timedelta(days=rnd.randint(0, 30))).isoformat(timespec="seconds") + "Z"))
    task_titles = ["Send revised quote", "Confirm site visit", "Follow up on proposal", "Collect signed contract", "Schedule technician", "Chase overdue invoice"]
    for i in range(30):
        done = rnd.random() < 0.35
        con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)", (f"T-{3001 + i}", rnd.choice(contacts), rnd.choice(task_titles), (date.today() + timedelta(days=rnd.randint(-5, 14))).isoformat(),
                    rnd.choice(owners), "done" if done else "open", (datetime.utcnow() - timedelta(days=rnd.randint(1, 20))).isoformat(timespec="seconds") + "Z", (datetime.utcnow().isoformat(timespec="seconds") + "Z") if done else None))
    note_texts = ["Called, asked for a revised quote with 3 units.", "Prefers WhatsApp over email.", "Site visit done; access via building security.", "Budget approved by finance, PO pending.", "Complained about late technician last month."]
    for i in range(45):
        con.execute("INSERT INTO notes VALUES (?,?,?,?,?)", (f"N-{4001 + i}", rnd.choice(contacts), rnd.choice(note_texts), rnd.choice(owners), (datetime.utcnow() - timedelta(days=rnd.randint(0, 60))).isoformat(timespec="seconds") + "Z"))
    inv = [("AC-SPLIT-18K", "Split AC unit 18k BTU", 14, 5, 1850.0, "CoolParts FZE"), ("AC-SPLIT-24K", "Split AC unit 24k BTU", 3, 5, 2350.0, "CoolParts FZE"),
           ("FILTER-STD", "Standard AC filter", 120, 40, 45.0, "CoolParts FZE"), ("GAS-R410A", "Refrigerant R410A 11.3 kg", 4, 6, 390.0, "GulfGas Supplies"),
           ("PUMP-0.5HP", "Water pump 0.5 HP", 9, 4, 620.0, "Nasser Trading"), ("PIPE-PVC-20", "PVC pipe 20 mm (6 m)", 210, 100, 18.0, "Nasser Trading"),
           ("CLEAN-KIT-PRO", "Deep-clean chemical kit", 0, 10, 240.0, "Skyline Facilities"), ("VAC-IND-30L", "Industrial vacuum 30 L", 6, 3, 1150.0, "Skyline Facilities"),
           ("THERMO-SMART", "Smart thermostat", 22, 10, 480.0, "CoolParts FZE"), ("DUCT-TAPE-AL", "Aluminium duct tape", 75, 30, 22.0, "Nasser Trading")]
    con.executemany("INSERT INTO inventory VALUES (?,?,?,?,?,?)", inv)
    for i in range(20):
        sku = rnd.choice(inv)
        q = rnd.randint(1, 6)
        con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", (f"SO-{5001 + i}", rnd.choice(contacts), sku[0], q, round(sku[4] * q, 2), rnd.choice(["confirmed", "shipped", "delivered"]), (datetime.utcnow() - timedelta(days=rnd.randint(0, 45))).isoformat(timespec="seconds") + "Z"))
    con.commit()
