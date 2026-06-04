# Retail Fabric IQ Demo Accelerator

A simple, end-to-end **Microsoft Fabric IQ** demo for a **Canadian retail** scenario.
It deploys a Lakehouse, synthetic data, an Eventhouse, a Direct Lake semantic model,
a Power BI report, a Fabric IQ Ontology, and an Activator into a **single workspace** —
plus config + guided steps for the preview-only AI agents.

> Built code-first with the **Fabric Core MCP server** used to discover item schemas,
> validate definitions, and derive the correct creation order. MCP guided the build;
> the deployment itself runs independently against the **Fabric REST API**.

**Target workspace:** set your own in `config/deploy.config.json` (`workspaceId` + `workspaceName`).

---

## 1. Architecture Overview

```
                        ┌──────────────────────────────────────────┐
                        │      Workspace: <your-workspace-name>      │
                        └──────────────────────────────────────────┘

  data/generate_data.py ──► notebooks/01_load_data ──► ┌───────────────┐
        (synthetic)            (PySpark job)            │  RetailLakehouse│  Delta tables:
                                                        │   (OneLake)     │  stores, products,
                                                        └───────┬─────────┘  customers, orders,
                                                                │            inventory, events
                                       Direct Lake (SQL endpoint)│
                                                                ▼
                                                       ┌─────────────────┐
                                                       │ RetailSalesModel │  measures +
                                                       │ (semantic model) │  relationships
                                                       └───────┬──────────┘
                                                               │ byConnection
                                                               ▼
                                                       ┌─────────────────┐
                                                       │ RetailDemoReport │  4 pages
                                                       └─────────────────┘

  eventhouse/setup.kql ──► ┌──────────────────┐        ┌─────────────────┐
       (InventoryEvents)   │ RetailEventhouse  │◄──────►│ LowInventory     │  alert when
                           │  + RetailEvents DB │        │ Activator (Reflex)│ on_hand < threshold
                           └──────────────────┘        └─────────────────┘

  ontology/retail_ontology.json ──► ┌─────────────────┐   Fabric IQ:
                                     │  RetailOntology  │   Store, Product, Customer,
                                     │ (+ GraphModel)   │   Order, Inventory + relationships
                                     └────────┬─────────┘
                                              │ grounds
                                              ▼
                          ┌───────────────┐      ┌────────────────────┐
                          │  Data Agent    │      │ Operations Agent    │
                          │ (NL Q&A, SDK)  │      │ (monitoring rules)  │
                          └───────────────┘      └────────────────────┘
```

### Components & relationships
| Component | Fabric item | Role | Depends on |
|---|---|---|---|
| **Lakehouse** | `RetailLakehouse` | Delta storage for all entities | — |
| **Load notebook** | `Retail_LoadData` | Generates + writes 6 Delta tables | Lakehouse |
| **Eventhouse** | `RetailEventhouse` (+ `RetailEvents` KQL DB) | Real-time low-stock / demand events | — |
| **Semantic model** | `RetailSalesModel` | **Direct Lake on SQL** model: relationships + measures | Lakehouse + SQL endpoint |
| **Report** | `RetailDemoReport` | 4-page Power BI report | Semantic model |
| **Ontology** | `RetailOntology` (+ GraphModel) | Fabric IQ entity graph (entities + relationships, data-bound) | Lakehouse tables |
| **Activator** | `LowInventoryActivator` (Reflex) | Triggers alert on low inventory | Eventhouse / inventory |
| **Data Agent** | guided (preview SDK) | NL business Q&A over the model + ontology | Semantic model + ontology |
| **Operations Agent** | guided (config) | Operational monitoring insights | Eventhouse + inventory |

---

## 2. Data Model

Synthetic, small but realistic. Volumes are configurable in `config/deploy.config.json`.

| Table | Grain | Key columns |
|---|---|---|
| `stores` | one row per store | store_id, name, city, province, type(online/instore), open_date |
| `products` | one row per product | product_id, name, category, unit_price, cost |
| `customers` | one row per customer | customer_id, name, city, province, segment, signup_date |
| `orders` | one row per order line | order_id, customer_id, store_id, product_id, qty, order_date, channel, amount |
| `inventory` | store × product | store_id, product_id, on_hand, reorder_threshold, updated_at |
| `events` | one row per event | event_time, store_id, product_id, event_type, severity, message |

**Default volumes:** 4 stores · 30 products · 80 customers · 350 orders · inventory per store×product · ~40 events (incl. ~15 low-stock).

**Relationships** (semantic model + ontology):
- Customer **places** Order  (`customers.customer_id → orders.customer_id`)
- Order **placed at** Store  (`stores.store_id → orders.store_id`)
- Order **contains** Product (`products.product_id → orders.product_id`)
- Product **has** Inventory  (`products.product_id → inventory.product_id`)
- Store **stocks** Inventory (`stores.store_id → inventory.store_id`)

**Measures:** Total Sales · Total Orders · Avg Order Value · Units Sold ·
Inventory On Hand · Low Stock Items (`on_hand < reorder_threshold`) ·
Sales by Store · Sales by Category.

---

## 3. Deployment Flow (dependency-aware)

```
1. Lakehouse              (RetailLakehouse)
2. Load notebook + run    -> Delta tables: stores, products, customers, orders, inventory, events
3. Eventhouse + KQL DB    (RetailEventhouse / RetailEvents)   [+ seed events]
4. Semantic model         (Direct Lake on OneLake over the lakehouse)
5. Power BI report        (byConnection -> semantic model)
6. Activator / Reflex     (low-inventory alert)
7-9. Fabric IQ preview    Ontology (deployed) + Data Agent + Operations Agent (guided)
```

Dependency rules enforced by the orchestrator:
- Lakehouse **before** load notebook **before** semantic model
- Semantic model **before** report and Data Agent
- Eventhouse **before** Operations Agent / Activator event scenario

---

## 4. Code / Scripts

```
fabric-mcp-core-demo/
  README.md                         # this file
  deploy_output.json                # deployed item IDs (generated)
  config/
    deploy.config.json              # workspace id, item names, thresholds, volumes
  deploy/
    fabric_client.py                # auth (Az token) + REST helpers + LRO polling
    deploy.py                       # orchestrator: ordered, --only / --skip flags
    report_builder.py               # generates the PBIR report (4 pages)
    ontology_builder.py             # generates the Fabric IQ ontology definition
    requirements.txt                # requests>=2.31
  data/
    generate_data.py                # standalone synthetic data generator (local preview)
  notebooks/
    01_load_data.py                 # Fabric notebook source (# CELL delimited)
    01_load_data.ipynb              # built ipynb (lakehouse-bound, run as a job)
  eventhouse/
    setup.kql                       # InventoryEvents table + seed events
  semantic_model/                   # TMDL (Direct Lake on SQL): tables, relationships, measures
    .platform, definition.pbism, definition/*.tmdl
  report/                           # PBIR report definition (generated)
  ontology/
    retail_ontology.json            # ontology spec (entities + relationships)
  agents/
    data_agent_config.yaml          # Data Agent config
    data_agent_setup.py             # fabric-data-agent SDK setup (run in a notebook)
    operations_agent_config.yaml    # Operations Agent monitoring rules
  activator/
    reflex_definition.json          # low-inventory Reflex definition (reference)
```

**Key implementation notes**
- **Auth:** `fabric_client.get_token()` shells out to `Get-AzAccessToken` (Az PowerShell).
  No `az` CLI or app registration needed — it reuses your interactive Fabric login.
- **Create item:** `POST /workspaces/{ws}/items` with `{displayName, type, definition}`;
  202 + `Location` long-running operation is polled to completion. `create_item` is idempotent.
- **Direct Lake on SQL endpoint:** the model reads the lakehouse via its **SQL analytics
  endpoint** (`Sql.Database("<sqlEndpoint>", "<sqlEndpointId>")`, partitions use
  `schemaName: dbo`). This uses **SSO**, so the report fetches data without a separately
  bound cloud credential. (Direct Lake *on OneLake* via `AzureStorage.DataLake` was tried
  first but renders blank when created programmatically because it needs a bound OneLake
  credential.) The model is **updated in place** (`updateDefinition`) so the report stays
  bound to the same id. Verified live: **Total Sales $106,412.12 across 350 orders**.
- **Ontology (Fabric IQ):** fully generated by `deploy/ontology_builder.py` as an
  Ontology item definition — 5 entity types (with properties + Lakehouse **data bindings**)
  and 5 relationship types (with **contextualizations** binding relationship instances to
  lakehouse columns). No manual authoring needed.
- **Notebook binding:** the built `.ipynb` carries `dependencies.lakehouse.default_lakehouse`
  metadata so `saveAsTable` writes into `RetailLakehouse`.
- **Report (PBIR):** bound `byConnection` (`semanticmodelid=<id>`). Schema versions matter —
  `report` = 2.0.0, `page`/`visualContainer` = 2.0.0, **`pagesMetadata` = 1.0.0**,
  `versionMetadata` = 1.0.0.

---

## 5. Config Files

- **`config/deploy.config.json`** — single source of truth: workspace id, item display names,
  `lowStockThreshold` (15), and data volumes (stores/products/customers/orders/events).
- **`ontology/retail_ontology.json`** — human-readable spec of the Fabric IQ ontology
  (mirrors what `deploy/ontology_builder.py` deploys): 5 entities (Store, Product,
  Customer, Order, Inventory) and their relationships, aligned to the semantic model.
- **`agents/data_agent_config.yaml`** — Data Agent: data sources (semantic model + ontology),
  sample questions ("top-selling products?", "highest-revenue store?", "low-inventory products?").
- **`agents/operations_agent_config.yaml`** — Operations Agent: monitoring rules
  (inventory below threshold, unusually high store demand) and insight templates.
- **`activator/reflex_definition.json`** — reference Reflex definition for the
  low-inventory trigger (alert when `on_hand < reorder_threshold`).
- **`eventhouse/setup.kql`** — creates `InventoryEvents` and seeds sample low-stock / demand events.

---

## 6. Execution Instructions

### Prerequisites
- Windows + **PowerShell** with **Az.Accounts** module
- **Python 3.10+**
- A Fabric workspace on a Fabric/Premium capacity (already provisioned)

### Steps
```powershell
# 0. Fork then clone your fork (swap <your-username>), or clone directly:
git clone https://github.com/<your-username>/fabric-mcp-core-demo.git
#    Source repo to fork: https://github.com/soocodes/fabric-mcp-core-demo
cd fabric-mcp-core-demo
pip install -r deploy/requirements.txt

# 1. Set YOUR environment: edit config/deploy.config.json and replace
#    "workspaceId" (and "workspaceName") with your own Fabric workspace.
#    Optionally adjust item names, thresholds, and data volumes.

# 2. Authenticate to Fabric (interactive)
Connect-AzAccount

# 3. (Optional) preview the synthetic data locally
python data/generate_data.py

# 4. Deploy everything (ordered, dependency-aware)
python deploy/deploy.py

#    …or deploy phases selectively:
python deploy/deploy.py --only lakehouse,data,semantic,report
python deploy/deploy.py --only eventhouse
python deploy/deploy.py --only activator,preview
python deploy/deploy.py --skip eventhouse
```

Item IDs for **your** deployment are written to **`deploy_output.json`** (git-ignored).

### Finish the preview / guided items
1. **Seed Eventhouse:** open the `RetailEvents` KQL database → new queryset →
   paste/run `eventhouse/setup.kql`.
2. **Activator rule:** open `LowInventoryActivator` → add a rule:
   *when `on_hand < reorder_threshold` → send alert*. (Reference: `activator/reflex_definition.json`.)
3. **Data Agent:** create a Fabric notebook, paste `agents/data_agent_setup.py`
   (uses the preview `fabric-data-agent` SDK), point it at `RetailSalesModel` + `RetailOntology`, run it.
4. **Operations Agent:** configure monitoring from `agents/operations_agent_config.yaml`.

### Demo storyline — "Maple & Pine, a Canadian retailer"

**Setup (the narrative):** *Maple & Pine* runs 4 stores across Ontario, Québec, BC and
Alberta plus an online channel. Revenue is healthy (**~$106K** over the demo period, 350
orders) but the ops team keeps getting surprised by stock-outs on popular items. Leadership
wants **one connected view** — and wants to *ask questions in plain English* instead of
waiting on report requests.

Walk the audience through the journey from **descriptive → diagnostic → conversational →
proactive** analytics:

**Act 1 — "What happened?" (Power BI report)**
- Open **RetailDemoReport → Sales overview**: Total Sales, Avg Order Value, Units Sold, and
  the Online-vs-In-Store split. *"This is the board-level pulse."*
- **Store performance**: which province pulls its weight; rank stores by revenue.
- **Top products**: the hero SKUs driving the category mix.
- Hook: *"Notice the report is **Direct Lake** — no import, no refresh lag; it reads the
  lakehouse Delta tables live."*

**Act 2 — "Why / how is it all connected?" (Fabric IQ Ontology)**
- Open **RetailOntology**: show the graph of business concepts —
  **Customer →places→ Order →contains→ Product →has→ Inventory**, and **Order →atStore→ Store**.
- Point out each entity is **data-bound** to a lakehouse table, so the ontology is a *living*
  business model, not a diagram. *"This is the shared language humans and AI agents both use."*

**Act 3 — "Just ask." (Data Agent)**
- In the **RetailDataAgent**, ask live:
  - *"Which store has the highest revenue?"*
  - *"What are the top-selling products?"*
  - *"Which products are low in inventory right now?"*
  - *"What's the average order value for Online vs In-Store?"*
- Emphasize it answers using the **semantic model measures + ontology relationships** — the
  same governed definitions behind the report. No SQL, no copy-paste.

**Act 4 — "Don't make me watch dashboards." (Operations Agent + Activator)**
- Show the **InventoryEvents** stream in **RetailEventhouse** (low-stock + demand-spike events).
- The **Operations Agent** surfaces insights like *"Product X at Store Y is below its reorder
  threshold."*
- The **LowInventoryActivator** turns that into action: *when `on_hand < reorder_threshold` →
  alert the store manager.* *"From a number on a dashboard to a notification in someone's inbox."*

**Close:** *"Same OneLake data, one semantic model, one ontology — powering a report, a
conversational agent, and an automated alert. That's Fabric IQ: descriptive, diagnostic,
conversational and proactive analytics on one copy of the data."*

> Tip: seed a fresh stock-out just before the demo (lower an `on_hand` value) so the
> Activator fires live during Act 4.

---

## 7. Known Gaps & Assumptions

| Area | Status | Note / workaround |
|---|---|---|
| Lakehouse, data load, semantic model, report, eventhouse, **ontology (entities + relationships, data-bound)**, activator item | ✅ Automated | Deployed live via REST. |
| **Report data** | ✅ Verified | Model returns **$106,412.12 / 350 orders** (Direct Lake on SQL). |
| **KQL seed** | ⚠️ Manual | Kusto data-plane token wasn't available in the headless run; run `eventhouse/setup.kql` once in the queryset. |
| **Activator alert rule** | ⚠️ Manual | Empty Reflex item is created; the alert rule is defined once in the Activator UI (Reflex rule JSON schema is not stable for headless authoring). |
| **Data Agent** | ⚠️ Preview SDK | No public REST CRUD; created via the `fabric-data-agent` SDK inside a notebook (`agents/data_agent_setup.py`). |
| **Operations Agent** | ⚠️ Preview | No public REST yet; delivered as config + monitoring rules to apply in-product. |
| **Direct Lake** | Design | Uses Direct Lake **on SQL endpoint** (SSO) — reliable for programmatic deploy. Direct Lake on OneLake needs a bound OneLake credential and renders blank when created headless. |
| **Capacity** | Assumption | Workspace is on an active Fabric capacity; the principal has admin/contributor rights. |
| **Auth** | Assumption | Interactive `Connect-AzAccount`; for CI use a service principal + `FABRIC_TOKEN`. The Power BI **DAX REST API is blocked** in this tenant — verify model data via report visuals or a `sempy` notebook, not `executeQueries`. |

### Deployed item IDs

Item IDs are **environment-specific** — they are generated on each deploy and written to
**`deploy_output.json`** in the repo root. The items created (by display name) are:

| Item | Type |
|---|---|
| RetailLakehouse | Lakehouse |
| Retail_LoadData | Notebook |
| RetailSalesModel | SemanticModel |
| RetailDemoReport | Report |
| RetailEventhouse | Eventhouse |
| RetailEvents | KQLDatabase |
| RetailOntology | Ontology (+GraphModel) |
| LowInventoryActivator | Reflex |
