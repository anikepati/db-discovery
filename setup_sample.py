"""
setup_sample.py — Create sample database and test data attributes.
"""

import json
import sqlite3

DB_PATH = "sample.db"
JSON_PATH = "data_attributes.json"


def create_sample_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            customer_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name      TEXT NOT NULL,
            last_name       TEXT NOT NULL,
            email           TEXT UNIQUE NOT NULL,
            phone           TEXT,
            address         TEXT,
            city            TEXT,
            state           TEXT,
            zip_code        TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE products (
            product_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name    TEXT NOT NULL,
            sku             TEXT UNIQUE NOT NULL,
            description     TEXT,
            unit_price      REAL NOT NULL,
            stock_quantity  INTEGER DEFAULT 0,
            category        TEXT
        );

        CREATE TABLE orders (
            order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id     INTEGER NOT NULL,
            order_date      DATETIME DEFAULT CURRENT_TIMESTAMP,
            status          TEXT DEFAULT 'pending',
            total_amount    REAL,
            shipping_address TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE order_items (
            item_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id        INTEGER NOT NULL,
            product_id      INTEGER NOT NULL,
            quantity        INTEGER NOT NULL,
            unit_price      REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(order_id),
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        );

        INSERT INTO customers (first_name, last_name, email, phone, city, state, zip_code)
        VALUES
            ('Alice', 'Johnson', 'alice@example.com', '555-0101', 'Seattle', 'WA', '98101'),
            ('Bob',   'Smith',   'bob@example.com',   '555-0102', 'Portland', 'OR', '97201');

        INSERT INTO products (product_name, sku, description, unit_price, stock_quantity, category)
        VALUES
            ('Wireless Mouse',     'WM-001', 'Ergonomic wireless mouse',  29.99, 150, 'Electronics'),
            ('Mechanical Keyboard','KB-002', 'RGB mechanical keyboard',   89.99,  75, 'Electronics'),
            ('USB-C Hub',          'HB-003', '7-port USB-C hub',          49.99, 200, 'Accessories');

        INSERT INTO orders (customer_id, status, total_amount)
        VALUES
            (1, 'shipped', 59.98),
            (2, 'pending', 89.99);

        INSERT INTO order_items (order_id, product_id, quantity, unit_price)
        VALUES
            (1, 1, 2, 29.99),
            (2, 2, 1, 89.99);
    """)
    conn.commit()
    conn.close()
    print(f"✅ Database created: {DB_PATH}")


def create_sample_json():
    data_attributes = {
        "attributes": [
            {"name": "customer email",        "value": "charlie@example.com",  "context": "Email address of a new customer signing up"},
            {"name": "customer first name",   "value": "Charlie",              "context": "First name of the new customer"},
            {"name": "customer last name",    "value": "Brown",                "context": "Last name of the new customer"},
            {"name": "customer phone number", "value": "555-0199",             "context": "Contact phone number for the customer"},
            {"name": "product sku code",      "value": "WM-001",              "context": "SKU identifier for the product being ordered"},
            {"name": "order quantity",        "value": 3,                     "context": "Number of units of the product being ordered"},
            {"name": "order status",          "value": "processing",          "context": "Updated status for the existing order belonging to alice@example.com"},
            {"name": "product stock level",   "value": 147,                   "context": "Updated inventory count for product SKU WM-001 after order fulfillment"},
            {"name": "loyalty points",        "value": 250,                   "context": "Loyalty reward points earned by the customer"},
        ]
    }
    with open(JSON_PATH, "w") as f:
        json.dump(data_attributes, f, indent=2)
    print(f"✅ Attributes created: {JSON_PATH}")


if __name__ == "__main__":
    create_sample_database()
    create_sample_json()
    print("\nRun:  adk run data_mapper_agent")
