# Fabric notebook source — Retail Data Agent setup (preview)
# Run this inside a Fabric notebook in the demo workspace. It uses the
# fabric-data-agent SDK to create + configure + publish the Data Agent that
# answers retail business questions over the semantic model and lakehouse.
#
# Install (first cell) if needed:  %pip install fabric-data-agent-sdk
# SDK is preview — method names may vary slightly by version; adjust as needed.

# CELL
%pip install -q fabric-data-agent-sdk

# CELL
from fabric.dataagent.client import (
    create_fabric_data_agent,
    FabricDataAgentManagement,
)

AGENT_NAME = "RetailDataAgent"

# Create (or open) the data agent item in the current workspace
agent = create_fabric_data_agent(AGENT_NAME)
mgmt = FabricDataAgentManagement(agent)

# CELL
# Grounding instructions
mgmt.update_configuration(
    instructions=(
        "You are a retail analytics assistant for a Canadian retailer. "
        "Currency is CAD. Prefer semantic-model measures (Total Sales, "
        "Avg Order Value, Inventory On Hand, Low Stock Items). For 'low "
        "inventory' compare inventory.on_hand to inventory.reorder_threshold. "
        "For 'top' items sort by Total Sales descending unless told otherwise."
    )
)

# CELL
# Add data sources: the Direct Lake semantic model (primary) + the lakehouse
mgmt.add_datasource("RetailSalesModel", type="semantic_model")
mgmt.add_datasource("RetailLakehouse", type="lakehouse")

# Select the relevant tables on the lakehouse source
for ds in mgmt.get_datasources():
    try:
        for tbl in ["stores", "products", "customers", "orders", "inventory", "events"]:
            ds.select(tbl)
    except Exception as e:
        print("select skipped for", ds, e)

# CELL
# Seed example questions (improves NL->query accuracy for the demo)
examples = [
    "What are the top-selling products?",
    "Which store has the highest revenue?",
    "Which products are low in inventory?",
    "What is the average order value by channel?",
    "How many orders came from Online vs In-Store?",
    "Which category drives the most sales?",
]
for q in examples:
    try:
        mgmt.add_example_query(q)
    except Exception as e:
        print("example skipped:", q, e)

# CELL
# Publish a read-only version for sharing / orchestration
published = mgmt.publish(description="Retail demo Q&A over sales + inventory.")
print("Published Data Agent:", published)
