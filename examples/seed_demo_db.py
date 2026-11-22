"""Create a small retail demo database at examples/demo.db."""

import random
import sqlite3
from pathlib import Path

DB = Path(__file__).with_name("demo.db")


def main() -> None:
    DB.unlink(missing_ok=True)
    random.seed(7)
    conn = sqlite3.connect(DB)
    conn.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, region TEXT);
        CREATE TABLE products  (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT, price REAL);
        CREATE TABLE orders    (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                                product_id INTEGER REFERENCES products(id), quantity INTEGER, order_date TEXT);
        CREATE TABLE salaries  (employee TEXT, amount REAL);  -- sensitive: keep off the allow-list
    """)
    regions = ["West", "East", "Central", "South"]
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?)",
                     [(i, f"Customer {i}", f"c{i}@example.com", random.choice(regions)) for i in range(1, 51)])
    products = [("Laptop", "Electronics", 1200), ("Monitor", "Electronics", 300), ("Desk", "Furniture", 450),
                ("Chair", "Furniture", 220), ("Keyboard", "Electronics", 80), ("Lamp", "Furniture", 60)]
    conn.executemany("INSERT INTO products VALUES (?,?,?,?)",
                     [(i + 1, *p) for i, p in enumerate(products)])
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)",
                     [(i, random.randint(1, 50), random.randint(1, 6), random.randint(1, 4),
                       f"2026-{random.randint(1, 10):02d}-{random.randint(1, 28):02d}") for i in range(1, 401)])
    conn.executemany("INSERT INTO salaries VALUES (?,?)", [("alice", 150000), ("bob", 140000)])
    conn.commit()
    conn.close()
    print(f"Created {DB}")


if __name__ == "__main__":
    main()
