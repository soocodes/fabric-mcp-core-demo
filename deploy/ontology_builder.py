"""
ontology_builder.py — generates a full Fabric IQ Ontology item definition.

Builds entity types (with properties + Lakehouse data bindings) and relationship
types (with contextualizations that bind relationship instances to lakehouse
columns), per the Fabric "Ontology definition" item format:
https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/ontology-definition

The result is a definition dict ({"parts":[...]}) ready for create/updateDefinition.
Data bindings point at the lakehouse SQL tables (schema 'dbo'), matching the
Direct Lake on SQL semantic model.
"""
import base64
import itertools
import json
import uuid

# value type mapping from our logical types -> ontology valueType
_VT = {"string": "String", "double": "Double", "integer": "BigInt",
       "bool": "Boolean", "datetime": "DateTime"}

# --- Retail domain definition -------------------------------------------------
# Each entity: name, source table, key column(s), display column, properties.
ENTITIES = [
    {"name": "Store", "table": "stores", "key": ["store_id"], "display": "name",
     "props": [("store_id", "string"), ("name", "string"), ("city", "string"),
               ("province", "string"), ("type", "string")]},
    {"name": "Product", "table": "products", "key": ["product_id"], "display": "name",
     "props": [("product_id", "string"), ("name", "string"),
               ("category", "string"), ("unit_price", "double")]},
    {"name": "Customer", "table": "customers", "key": ["customer_id"], "display": "name",
     "props": [("customer_id", "string"), ("name", "string"),
               ("segment", "string"), ("province", "string")]},
    {"name": "Order", "table": "orders", "key": ["order_id"], "display": "order_id",
     "props": [("order_id", "string"), ("qty", "integer"), ("amount", "double"),
               ("channel", "string"), ("order_date", "string")]},
    {"name": "Inventory", "table": "inventory", "key": ["store_id", "product_id"],
     "display": "product_id",
     "props": [("store_id", "string"), ("product_id", "string"),
               ("on_hand", "integer"), ("reorder_threshold", "integer")]},
]

# relationships: name, source entity, target entity, binding table,
# source-key column->prop, target-key column(s)->prop
RELATIONSHIPS = [
    {"name": "places", "source": "Customer", "target": "Order", "table": "orders",
     "srcKeys": [("customer_id", "customer_id")], "tgtKeys": [("order_id", "order_id")]},
    {"name": "contains", "source": "Order", "target": "Product", "table": "orders",
     "srcKeys": [("order_id", "order_id")], "tgtKeys": [("product_id", "product_id")]},
    {"name": "atStore", "source": "Order", "target": "Store", "table": "orders",
     "srcKeys": [("order_id", "order_id")], "tgtKeys": [("store_id", "store_id")]},
    {"name": "hasInventory", "source": "Product", "target": "Inventory", "table": "inventory",
     "srcKeys": [("product_id", "product_id")],
     "tgtKeys": [("store_id", "store_id"), ("product_id", "product_id")]},
    {"name": "holdsInventory", "source": "Store", "target": "Inventory", "table": "inventory",
     "srcKeys": [("store_id", "store_id")],
     "tgtKeys": [("store_id", "store_id"), ("product_id", "product_id")]},
]


def _part(path: str, obj) -> dict:
    payload = json.dumps(obj, indent=2)
    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return {"path": path, "payload": b64, "payloadType": "InlineBase64"}


def build(workspace_id: str, lakehouse_id: str, schema: str = "dbo") -> dict:
    """Return an ontology item definition dict bound to the given lakehouse."""
    ids = itertools.count(1000000000001)  # positive 64-bit, unique per ontology

    # Resolve entity + property ids first so relationships can reference them.
    model = {}  # entity name -> {"id":.., "props":{col:id}, ...}
    for e in ENTITIES:
        eid = str(next(ids))
        prop_ids = {col: str(next(ids)) for col, _ in e["props"]}
        model[e["name"]] = {"id": eid, "props": prop_ids, "spec": e}

    parts = [_part("definition.json", {})]

    # ---- Entity types + data bindings ----
    for e in ENTITIES:
        m = model[e["name"]]
        eid = m["id"]
        properties = [{
            "id": m["props"][col], "name": col, "redefines": None,
            "baseTypeNamespaceType": None, "valueType": _VT[t],
        } for col, t in e["props"]]
        entity_type = {
            "id": eid, "namespace": "usertypes", "baseEntityTypeId": None,
            "name": e["name"], "entityIdParts": [m["props"][k] for k in e["key"]],
            "displayNamePropertyId": m["props"][e["display"]],
            "namespaceType": "Custom", "visibility": "Visible",
            "properties": properties, "timeseriesProperties": [],
        }
        parts.append(_part(f"EntityTypes/{eid}/definition.json", entity_type))

        binding = {
            "id": str(uuid.uuid4()),
            "dataBindingConfiguration": {
                "dataBindingType": "NonTimeSeries",
                "propertyBindings": [
                    {"sourceColumnName": col, "targetPropertyId": m["props"][col]}
                    for col, _ in e["props"]
                ],
                "sourceTableProperties": {
                    "sourceType": "LakehouseTable",
                    "workspaceId": workspace_id,
                    "itemId": lakehouse_id,
                    "sourceTableName": e["table"],
                    "sourceSchema": schema,
                },
            },
        }
        parts.append(_part(
            f"EntityTypes/{eid}/DataBindings/{binding['id']}.json", binding))

    # ---- Relationship types + contextualizations ----
    for r in RELATIONSHIPS:
        rid = str(next(ids))
        src, tgt = model[r["source"]], model[r["target"]]
        rel_type = {
            "namespace": "usertypes", "id": rid, "name": r["name"],
            "namespaceType": "Custom",
            "source": {"entityTypeId": src["id"]},
            "target": {"entityTypeId": tgt["id"]},
        }
        parts.append(_part(f"RelationshipTypes/{rid}/definition.json", rel_type))

        ctx = {
            "id": str(uuid.uuid4()),
            "dataBindingTable": {
                "workspaceId": workspace_id, "itemId": lakehouse_id,
                "sourceTableName": r["table"], "sourceSchema": schema,
                "sourceType": "LakehouseTable",
            },
            "sourceKeyRefBindings": [
                {"sourceColumnName": col, "targetPropertyId": src["props"][prop]}
                for col, prop in r["srcKeys"]
            ],
            "targetKeyRefBindings": [
                {"sourceColumnName": col, "targetPropertyId": tgt["props"][prop]}
                for col, prop in r["tgtKeys"]
            ],
        }
        parts.append(_part(
            f"RelationshipTypes/{rid}/Contextualizations/{ctx['id']}.json", ctx))

    return {"parts": parts}


if __name__ == "__main__":
    d = build("WS", "LH")
    print(f"{len(d['parts'])} parts")
    for p in d["parts"]:
        print(" ", p["path"])
