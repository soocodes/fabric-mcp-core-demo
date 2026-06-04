"""
generate_data.py
----------------
Standalone synthetic Canadian-retail data generator. No external services needed.

Run locally to preview the dataset:
    python data/generate_data.py --out ./_preview

Produces 6 CSV files: stores, products, customers, orders, inventory, events.
The SAME generation logic is embedded in notebooks/01_load_data.ipynb, which
writes the data as Delta tables directly into the Fabric Lakehouse.

Only the standard library is used (random, csv, datetime) so it runs anywhere.
"""

import argparse
import csv
import os
import random
from datetime import datetime, timedelta

CATEGORIES = {
    "Apparel": ["T-Shirt", "Jeans", "Jacket", "Sweater", "Socks"],
    "Footwear": ["Sneakers", "Boots", "Sandals", "Running Shoes"],
    "Electronics": ["Headphones", "Smartwatch", "Bluetooth Speaker", "Power Bank"],
    "Home": ["Mug", "Cushion", "Lamp", "Blanket", "Water Bottle"],
    "Outdoor": ["Backpack", "Tent", "Camp Chair", "Cooler"],
}
PROVINCES = [("Toronto", "ON"), ("Vancouver", "BC"), ("Montreal", "QC"), ("Calgary", "AB")]
SEGMENTS = ["Loyal", "New", "Occasional", "VIP"]
CHANNELS = ["Online", "In-Store"]
EVENT_TYPES = ["LowStock", "SalesSpike", "Restock", "StockOut"]


def _money(v):
    return round(v, 2)


def generate(cfg):
    rnd = random.Random(cfg["seed"])
    today = datetime(2026, 6, 1)

    # Stores (mix of online + physical)
    stores = []
    for i in range(cfg["stores"]):
        city, prov = PROVINCES[i % len(PROVINCES)]
        is_online = i == 0  # first store is the online channel hub
        stores.append({
            "store_id": f"S{i+1:03d}",
            "name": ("Online Store" if is_online else f"{city} Flagship"),
            "city": city,
            "province": prov,
            "type": "Online" if is_online else "In-Store",
            "open_date": (today - timedelta(days=rnd.randint(400, 1500))).strftime("%Y-%m-%d"),
        })

    # Products
    products = []
    pid = 1
    flat = [(c, n) for c, names in CATEGORIES.items() for n in names]
    while len(products) < cfg["products"]:
        cat, base = flat[(pid - 1) % len(flat)]
        cost = _money(rnd.uniform(5, 120))
        products.append({
            "product_id": f"P{pid:03d}",
            "name": f"{base} {chr(64 + (pid % 5) + 1)}",
            "category": cat,
            "unit_price": _money(cost * rnd.uniform(1.3, 2.2)),
            "cost": cost,
        })
        pid += 1

    # Customers
    customers = []
    first = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Quinn", "Jamie"]
    last = ["Tremblay", "Smith", "Roy", "Nguyen", "Patel", "Brown", "Lee", "Martin", "Singh", "Wong"]
    for i in range(cfg["customers"]):
        city, prov = rnd.choice(PROVINCES)
        customers.append({
            "customer_id": f"C{i+1:04d}",
            "name": f"{rnd.choice(first)} {rnd.choice(last)}",
            "city": city,
            "province": prov,
            "segment": rnd.choices(SEGMENTS, weights=[4, 3, 2, 1])[0],
            "signup_date": (today - timedelta(days=rnd.randint(10, 1200))).strftime("%Y-%m-%d"),
        })

    # Orders (one product line per order row to keep the model simple)
    orders = []
    for i in range(cfg["orders"]):
        cust = rnd.choice(customers)
        prod = rnd.choice(products)
        store = rnd.choice(stores)
        qty = rnd.randint(1, 5)
        channel = "Online" if store["type"] == "Online" else rnd.choice(CHANNELS)
        odate = today - timedelta(days=rnd.randint(0, 120), hours=rnd.randint(0, 23))
        orders.append({
            "order_id": f"O{i+1:05d}",
            "customer_id": cust["customer_id"],
            "store_id": store["store_id"],
            "product_id": prod["product_id"],
            "qty": qty,
            "order_date": odate.strftime("%Y-%m-%d %H:%M:%S"),
            "channel": channel,
            "amount": _money(qty * prod["unit_price"]),
        })

    # Inventory (per store x product), some intentionally below threshold for demo
    inventory = []
    thr = cfg["lowStock"]
    for s in stores:
        for p in products:
            on_hand = rnd.choice([rnd.randint(0, thr - 1)] + [rnd.randint(thr, 200)] * 5)
            inventory.append({
                "store_id": s["store_id"],
                "product_id": p["product_id"],
                "on_hand": on_hand,
                "reorder_threshold": thr,
                "updated_at": today.strftime("%Y-%m-%d %H:%M:%S"),
            })

    # Events derived from low inventory + random spikes
    events = []
    low = [r for r in inventory if r["on_hand"] < thr]
    rnd.shuffle(low)
    for r in low[: cfg["events"] // 2]:
        events.append({
            "event_time": (today - timedelta(minutes=rnd.randint(1, 4000))).strftime("%Y-%m-%d %H:%M:%S"),
            "store_id": r["store_id"],
            "product_id": r["product_id"],
            "event_type": "StockOut" if r["on_hand"] == 0 else "LowStock",
            "severity": "High" if r["on_hand"] == 0 else "Medium",
            "message": f"{r['product_id']} at {r['store_id']} on_hand={r['on_hand']} (<{thr})",
        })
    while len(events) < cfg["events"]:
        s = rnd.choice(stores)
        p = rnd.choice(products)
        events.append({
            "event_time": (today - timedelta(minutes=rnd.randint(1, 4000))).strftime("%Y-%m-%d %H:%M:%S"),
            "store_id": s["store_id"],
            "product_id": p["product_id"],
            "event_type": "SalesSpike",
            "severity": "Low",
            "message": f"High demand for {p['product_id']} at {s['store_id']}",
        })
    events.sort(key=lambda e: e["event_time"])

    return {
        "stores": stores, "products": products, "customers": customers,
        "orders": orders, "inventory": inventory, "events": events,
    }


def write_csv(data, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, rows in data.items():
        path = os.path.join(out_dir, f"{name}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"  wrote {len(rows):4d} rows -> {path}")


DEFAULT_CFG = {"stores": 4, "products": 30, "customers": 80, "orders": 350,
               "events": 40, "seed": 42, "lowStock": 15}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./_preview")
    args = ap.parse_args()
    d = generate(DEFAULT_CFG)
    write_csv(d, args.out)
    print("Done.")
