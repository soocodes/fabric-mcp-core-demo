# Fabric notebook source — Retail_LoadData
# This file is converted to a Fabric notebook (.ipynb) by deploy/deploy.py, which also
# injects the default-lakehouse binding. Cells are separated by lines starting with "# CELL".
# It is self-contained (no external files) so it runs as a Fabric notebook job.

# CELL markdown
# # Retail Demo — Data Loader
# Generates small synthetic Canadian-retail data and writes Delta tables to the attached Lakehouse.

# CELL
import random
from datetime import datetime, timedelta
import pandas as pd

SEED = 42
N_STORES, N_PRODUCTS, N_CUSTOMERS, N_ORDERS, N_EVENTS = 4, 30, 80, 350, 40
LOW_STOCK = 15
rnd = random.Random(SEED)
today = datetime(2026, 6, 1)

CATEGORIES = {
    "Apparel": ["T-Shirt", "Jeans", "Jacket", "Sweater", "Socks"],
    "Footwear": ["Sneakers", "Boots", "Sandals", "Running Shoes"],
    "Electronics": ["Headphones", "Smartwatch", "Bluetooth Speaker", "Power Bank"],
    "Home": ["Mug", "Cushion", "Lamp", "Blanket", "Water Bottle"],
    "Outdoor": ["Backpack", "Tent", "Camp Chair", "Cooler"],
}
PROVINCES = [("Toronto", "ON"), ("Vancouver", "BC"), ("Montreal", "QC"), ("Calgary", "AB")]
SEGMENTS = ["Loyal", "New", "Occasional", "VIP"]
m = lambda v: round(v, 2)

# CELL
# Stores
stores = []
for i in range(N_STORES):
    city, prov = PROVINCES[i % len(PROVINCES)]
    online = i == 0
    stores.append(dict(store_id=f"S{i+1:03d}",
                       name=("Online Store" if online else f"{city} Flagship"),
                       city=city, province=prov,
                       type=("Online" if online else "In-Store"),
                       open_date=(today - timedelta(days=rnd.randint(400, 1500))).strftime("%Y-%m-%d")))

# Products
products, flat, pid = [], [(c, n) for c, ns in CATEGORIES.items() for n in ns], 1
while len(products) < N_PRODUCTS:
    cat, base = flat[(pid - 1) % len(flat)]
    cost = m(rnd.uniform(5, 120))
    products.append(dict(product_id=f"P{pid:03d}", name=f"{base} {chr(64 + (pid % 5) + 1)}",
                         category=cat, unit_price=m(cost * rnd.uniform(1.3, 2.2)), cost=cost))
    pid += 1

# Customers
first = ["Alex","Sam","Jordan","Taylor","Morgan","Casey","Riley","Avery","Quinn","Jamie"]
last = ["Tremblay","Smith","Roy","Nguyen","Patel","Brown","Lee","Martin","Singh","Wong"]
customers = []
for i in range(N_CUSTOMERS):
    city, prov = rnd.choice(PROVINCES)
    customers.append(dict(customer_id=f"C{i+1:04d}", name=f"{rnd.choice(first)} {rnd.choice(last)}",
                          city=city, province=prov,
                          segment=rnd.choices(SEGMENTS, weights=[4,3,2,1])[0],
                          signup_date=(today - timedelta(days=rnd.randint(10,1200))).strftime("%Y-%m-%d")))

# Orders
orders = []
for i in range(N_ORDERS):
    c, p, s = rnd.choice(customers), rnd.choice(products), rnd.choice(stores)
    qty = rnd.randint(1, 5)
    channel = "Online" if s["type"] == "Online" else rnd.choice(["Online", "In-Store"])
    od = today - timedelta(days=rnd.randint(0, 120), hours=rnd.randint(0, 23))
    orders.append(dict(order_id=f"O{i+1:05d}", customer_id=c["customer_id"], store_id=s["store_id"],
                       product_id=p["product_id"], qty=qty,
                       order_date=od.strftime("%Y-%m-%d %H:%M:%S"), channel=channel,
                       amount=m(qty * p["unit_price"])))

# Inventory (some below threshold on purpose)
inventory = []
for s in stores:
    for p in products:
        on_hand = rnd.choice([rnd.randint(0, LOW_STOCK - 1)] + [rnd.randint(LOW_STOCK, 200)] * 5)
        inventory.append(dict(store_id=s["store_id"], product_id=p["product_id"], on_hand=on_hand,
                              reorder_threshold=LOW_STOCK, updated_at=today.strftime("%Y-%m-%d %H:%M:%S")))

# Events
events, low = [], [r for r in inventory if r["on_hand"] < LOW_STOCK]
rnd.shuffle(low)
for r in low[: N_EVENTS // 2]:
    events.append(dict(event_time=(today - timedelta(minutes=rnd.randint(1,4000))).strftime("%Y-%m-%d %H:%M:%S"),
                       store_id=r["store_id"], product_id=r["product_id"],
                       event_type=("StockOut" if r["on_hand"] == 0 else "LowStock"),
                       severity=("High" if r["on_hand"] == 0 else "Medium"),
                       message=f"{r['product_id']} at {r['store_id']} on_hand={r['on_hand']} (<{LOW_STOCK})"))
while len(events) < N_EVENTS:
    s, p = rnd.choice(stores), rnd.choice(products)
    events.append(dict(event_time=(today - timedelta(minutes=rnd.randint(1,4000))).strftime("%Y-%m-%d %H:%M:%S"),
                       store_id=s["store_id"], product_id=p["product_id"], event_type="SalesSpike",
                       severity="Low", message=f"High demand for {p['product_id']} at {s['store_id']}"))
print("generated:", len(stores), len(products), len(customers), len(orders), len(inventory), len(events))

# CELL
# Write Delta tables to the default lakehouse
tables = dict(stores=stores, products=products, customers=customers,
              orders=orders, inventory=inventory, events=events)
for name, rows in tables.items():
    pdf = pd.DataFrame(rows)
    sdf = spark.createDataFrame(pdf)
    sdf.write.mode("overwrite").format("delta").saveAsTable(name)
    print(f"wrote table {name}: {sdf.count()} rows")

# CELL
# Verify
for name in tables:
    spark.sql(f"SELECT '{name}' AS tbl, COUNT(*) AS rows FROM {name}").show()
print("Load complete.")
