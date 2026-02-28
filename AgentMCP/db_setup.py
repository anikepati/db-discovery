"""
db_setup.py
-----------
Creates a demo SQLite database with realistic sample data
so all SQL tools work out of the box without any external DB.

Run once before starting the agent:
    python db_setup.py
"""

import sqlite3
import random
from datetime import datetime, timedelta

DB_PATH = "./demo.db"


def random_date(days_back: int = 365) -> str:
    d = datetime.now() - timedelta(days=random.randint(0, days_back))
    return d.strftime("%Y-%m-%d")


def setup():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # ── customers ─────────────────────────────────────────────────────────────
    cur.execute("DROP TABLE IF EXISTS customers")
    cur.execute("""
        CREATE TABLE customers (
            id          INTEGER PRIMARY KEY,
            name        TEXT,
            email       TEXT,
            status      TEXT,
            created_at  TEXT
        )
    """)
    statuses = ["active", "inactive", "lead"]
    customers = [
        (i, f"Customer {i}", f"customer{i}@example.com",
         random.choice(statuses), random_date(730))
        for i in range(1, 51)
    ]
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    # ── customer_notes ────────────────────────────────────────────────────────
    cur.execute("DROP TABLE IF EXISTS customer_notes")
    cur.execute("""
        CREATE TABLE customer_notes (
            id          INTEGER PRIMARY KEY,
            customer_id INTEGER,
            note        TEXT,
            created_at  TEXT
        )
    """)
    notes = [
        (i, random.randint(1, 50),
         random.choice([
             "Called regarding renewal",
             "Submitted support ticket",
             "Upgraded plan",
             "Requested demo",
             "Payment failed",
         ]),
         random_date(180))
        for i in range(1, 101)
    ]
    cur.executemany("INSERT INTO customer_notes VALUES (?,?,?,?)", notes)

    # ── invoices ──────────────────────────────────────────────────────────────
    cur.execute("DROP TABLE IF EXISTS invoices")
    cur.execute("""
        CREATE TABLE invoices (
            id          INTEGER PRIMARY KEY,
            customer_id INTEGER,
            amount      REAL,
            status      TEXT,
            due_date    TEXT,
            paid_at     TEXT
        )
    """)
    inv_statuses = ["paid", "unpaid", "overdue"]
    invoices = []
    for i in range(1, 201):
        status  = random.choice(inv_statuses)
        due     = random_date(120)
        paid_at = random_date(90) if status == "paid" else None
        invoices.append((
            i,
            random.randint(1, 50),
            round(random.uniform(100, 10000), 2),
            status,
            due,
            paid_at,
        ))
    cur.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?)", invoices)

    # ── employees ─────────────────────────────────────────────────────────────
    cur.execute("DROP TABLE IF EXISTS employees")
    cur.execute("""
        CREATE TABLE employees (
            id          INTEGER PRIMARY KEY,
            name        TEXT,
            email       TEXT,
            department  TEXT,
            hire_date   TEXT,
            salary      REAL,
            status      TEXT
        )
    """)
    departments = ["Engineering", "Sales", "HR", "Finance", "Marketing", "Support"]
    employees = [
        (
            i,
            f"Employee {i}",
            f"emp{i}@company.com",
            random.choice(departments),
            random_date(2000),
            round(random.uniform(50000, 150000), 2),
            random.choice(["active", "active", "active", "inactive"]),
        )
        for i in range(1, 101)
    ]
    cur.executemany("INSERT INTO employees VALUES (?,?,?,?,?,?,?)", employees)

    conn.commit()
    conn.close()

    print(f"✅ Demo database created at {DB_PATH}")
    print(f"   customers:      50 rows")
    print(f"   customer_notes: 100 rows")
    print(f"   invoices:       200 rows")
    print(f"   employees:      100 rows")


if __name__ == "__main__":
    setup()
