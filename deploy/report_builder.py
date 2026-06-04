"""
report_builder.py
-----------------
Generates a PBIR (Power BI enhanced report format) report definition for the
retail demo and writes it under report/. The report binds to the deployed
semantic model via `definition.pbir` (byConnection / semanticmodelid).

PBIR structure produced:
  report/
    definition.pbir
    definition/report.json
    definition/version.json
    definition/pages/pages.json
    definition/pages/<pageId>/page.json
    definition/pages/<pageId>/visuals/<visualId>/visual.json

Visuals are described with a compact spec and expanded to valid visual.json.
Measures live on their home table (e.g. 'Total Sales' on entity 'orders').
"""

import hashlib
import json
from pathlib import Path

PAGE_W, PAGE_H = 1280, 720


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _col(entity, prop):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def _measure(entity, prop):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def _proj(field, qref, active=None):
    p = {"field": field, "queryRef": qref}
    if active is not None:
        p["active"] = active
    return p


# ---- compact field helpers -------------------------------------------------
def C(entity, prop):
    return ("col", entity, prop)


def M(entity, prop):
    return ("mea", entity, prop)


def _field(spec):
    kind, entity, prop = spec
    if kind == "col":
        return _col(entity, prop), f"{entity}.{prop}"
    return _measure(entity, prop), f"{entity}.{prop}"


def _role(specs):
    projections = []
    for s in specs:
        field, qref = _field(s)
        projections.append(_proj(field, qref))
    return {"projections": projections}


def visual(vtype, x, y, w, h, roles, sort=None, title=None):
    query_state = {role: _role(specs) for role, specs in roles.items()}
    v = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
        "name": "",  # set later
        "position": {"x": x, "y": y, "z": 0, "height": h, "width": w, "tabOrder": 0},
        "visual": {
            "visualType": vtype,
            "query": {"queryState": query_state},
            "drillFilterOtherVisuals": True,
        },
    }
    if sort:
        field, _ = _field(sort[0])
        v["visual"]["query"]["sortDefinition"] = {
            "sort": [{"field": field, "direction": sort[1]}]
        }
    if title:
        v["visual"]["visualContainerObjects"] = {
            "title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}},
                                       "show": {"expr": {"Literal": {"Value": "true"}}}}}]
        }
    return v


# ---- page definitions ------------------------------------------------------
def _pages():
    return [
        ("Sales Overview", [
            visual("card", 20, 20, 280, 120, {"Values": [M("orders", "Total Sales")]}),
            visual("card", 320, 20, 280, 120, {"Values": [M("orders", "Total Orders")]}),
            visual("card", 620, 20, 280, 120, {"Values": [M("orders", "Avg Order Value")]}),
            visual("clusteredColumnChart", 20, 160, 600, 300,
                   {"Category": [C("products", "category")], "Y": [M("orders", "Total Sales")]},
                   sort=(M("orders", "Total Sales"), "Descending"), title="Sales by Category"),
            visual("donutChart", 640, 160, 420, 300,
                   {"Category": [C("orders", "channel")], "Y": [M("orders", "Total Sales")]},
                   title="Sales by Channel"),
        ]),
        ("Store Performance", [
            visual("card", 20, 20, 280, 120, {"Values": [M("orders", "Total Sales")]}),
            visual("clusteredBarChart", 20, 160, 600, 400,
                   {"Category": [C("stores", "name")], "Y": [M("orders", "Total Sales")]},
                   sort=(M("orders", "Total Sales"), "Descending"), title="Sales by Store"),
            visual("tableEx", 640, 160, 600, 400,
                   {"Values": [C("stores", "name"), C("stores", "city"),
                               M("orders", "Total Sales"), M("orders", "Total Orders")]},
                   title="Store Detail"),
        ]),
        ("Inventory Status", [
            visual("card", 20, 20, 280, 120, {"Values": [M("inventory", "Inventory On Hand")]}),
            visual("card", 320, 20, 280, 120, {"Values": [M("inventory", "Low Stock Items")]}),
            visual("card", 620, 20, 280, 120, {"Values": [M("inventory", "Inventory Health %")]}),
            visual("clusteredColumnChart", 20, 160, 600, 400,
                   {"Category": [C("stores", "name")], "Y": [M("inventory", "Inventory On Hand")]},
                   title="On-Hand by Store"),
            visual("tableEx", 640, 160, 600, 400,
                   {"Values": [C("products", "category"), M("inventory", "Inventory On Hand"),
                               M("inventory", "Low Stock Items")]},
                   title="Inventory by Category"),
        ]),
        ("Top Products", [
            visual("clusteredBarChart", 20, 20, 600, 540,
                   {"Category": [C("products", "name")], "Y": [M("orders", "Total Sales")]},
                   sort=(M("orders", "Total Sales"), "Descending"), title="Top Products by Sales"),
            visual("tableEx", 640, 20, 600, 540,
                   {"Values": [C("products", "name"), C("products", "category"),
                               M("orders", "Units Sold"), M("orders", "Total Sales")]},
                   title="Product Detail"),
        ]),
    ]


def build(semantic_model_id: str, out_dir: Path):
    out_dir = Path(out_dir)
    defn = out_dir / "definition"
    pages_dir = defn / "pages"
    (out_dir).mkdir(parents=True, exist_ok=True)

    # definition.pbir (bind to semantic model in workspace)
    pbir = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byConnection": {"connectionString": f"semanticmodelid={semantic_model_id}"}},
    }
    _write(out_dir / "definition.pbir", pbir)

    # report.json + version.json
    _write(defn / "report.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/2.0.0/schema.json",
        "themeCollection": {
            "baseTheme": {
                "name": "CY24SU10",
                "reportVersionAtImport": "2.0.0",
                "type": "SharedResources",
            }
        },
        "settings": {"useStylableVisualContainerHeader": True},
    })
    _write(defn / "version.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    })

    page_ids = []
    for idx, (display, visuals) in enumerate(_pages()):
        pid = _id("page", display)
        page_ids.append(pid)
        pdir = pages_dir / pid
        _write(pdir / "page.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
            "name": pid,
            "displayName": display,
            "displayOption": "FitToPage",
            "height": PAGE_H,
            "width": PAGE_W,
        })
        for vi, v in enumerate(visuals):
            vid = _id("vis", display, str(vi))
            v["name"] = vid
            v["position"]["z"] = vi
            v["position"]["tabOrder"] = vi
            _write(pdir / "visuals" / vid / "visual.json", v)

    _write(pages_dir / "pages.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": page_ids,
        "activePageName": page_ids[0],
    })
    return out_dir


def _write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import sys
    smid = sys.argv[1] if len(sys.argv) > 1 else "00000000-0000-0000-0000-000000000000"
    here = Path(__file__).resolve().parents[1] / "report"
    build(smid, here)
    print(f"Report PBIR written to {here}")
