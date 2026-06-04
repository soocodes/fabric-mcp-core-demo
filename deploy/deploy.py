"""
deploy.py — Retail Fabric IQ demo orchestrator
==============================================
Deploys the demo artifacts into a single Fabric workspace in dependency order:

  1. Lakehouse                     (RetailLakehouse)
  2. Load notebook + run job       -> Delta tables (stores/products/.../events)
  3. Eventhouse + KQL database     (RetailEventhouse / RetailEvents) + seed events
  4. Semantic model (Direct Lake on OneLake)
  5. Power BI report (PBIR, bound to the semantic model)
  6. Activator / Reflex            (low-inventory trigger)
  7/8/9. Ontology + Data Agent + Operations Agent (preview -> guided)

Each phase is guarded: a failure in one preview/optional phase does not abort
the automatable core. Item IDs are printed and saved to deploy_output.json.

Auth: reuses the local Az PowerShell session (see fabric_client.get_token).

Usage:
    python deploy/deploy.py                 # full deployment
    python deploy/deploy.py --skip eventhouse,activator
    python deploy/deploy.py --only lakehouse,data,semantic,report
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from fabric_client import FabricClient, get_token, part, definition_from_folder  # noqa: E402
import report_builder  # noqa: E402

CFG = json.loads((ROOT / "config" / "deploy.config.json").read_text())
WS = CFG["workspaceId"]
N = CFG["names"]
results = {}


# --------------------------------------------------------------------------
# Notebook (.py -> ipynb) builder
# --------------------------------------------------------------------------
def build_notebook_ipynb(py_path: Path, lakehouse_id: str) -> str:
    """Convert a '# CELL'-delimited .py source into a Fabric notebook ipynb JSON
    string, binding the default lakehouse so saveAsTable writes to it."""
    raw = py_path.read_text(encoding="utf-8")
    blocks, cur, cell_type = [], [], "code"
    for line in raw.splitlines():
        if line.startswith("# CELL"):
            if cur:
                blocks.append((cell_type, cur))
            cur = []
            cell_type = "markdown" if "markdown" in line else "code"
            continue
        cur.append(line)
    if cur:
        blocks.append((cell_type, cur))

    cells = []
    for ctype, lines in blocks:
        # strip leading blank lines
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue
        if ctype == "markdown":
            src = [l[2:] if l.startswith("# ") else l.lstrip("#").lstrip() for l in lines]
            cells.append({"cell_type": "markdown", "metadata": {}, "source": _joined(src)})
        else:
            cells.append({"cell_type": "code", "metadata": {}, "execution_count": None,
                          "outputs": [], "source": _joined(lines)})

    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "language_info": {"name": "python"},
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
            "dependencies": {
                "lakehouse": {
                    "default_lakehouse": lakehouse_id,
                    "default_lakehouse_name": N["lakehouse"],
                    "default_lakehouse_workspace_id": WS,
                }
            },
        },
        "cells": cells,
    }
    return json.dumps(nb, indent=1)


def _joined(lines):
    return [l + "\n" for l in lines[:-1]] + [lines[-1]] if lines else []


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------
def phase_lakehouse(fc: FabricClient):
    print("\n[1] Lakehouse")
    lh = fc.create_item(WS, N["lakehouse"], "Lakehouse",
                        description="Retail demo Delta tables")
    results["lakehouse"] = lh["id"]
    return lh["id"]


def phase_data(fc: FabricClient, lakehouse_id: str):
    print("\n[2] Load notebook + run")
    ipynb = build_notebook_ipynb(ROOT / "notebooks" / "01_load_data.py", lakehouse_id)
    (ROOT / "notebooks" / "01_load_data.ipynb").write_text(ipynb, encoding="utf-8")
    definition = {"format": "ipynb", "parts": [part("notebook-content.ipynb", ipynb)]}
    nb = fc.create_item(WS, N["loadNotebook"], "Notebook", definition=definition,
                        description="Generates + loads retail Delta tables")
    results["loadNotebook"] = nb["id"]
    print("    running notebook job (generates + writes Delta tables)...")
    fc.run_notebook(WS, nb["id"])
    print("    data load complete")


def phase_eventhouse(fc: FabricClient):
    print("\n[3] Eventhouse + KQL database")
    eh = fc.create_item(WS, N["eventhouse"], "Eventhouse",
                        description="Retail real-time events")
    results["eventhouse"] = eh["id"]
    # Ensure a KQL database exists under the eventhouse
    kql = fc.get_item_by_name(WS, N["kqlDatabase"], "KQLDatabase")
    if not kql:
        r = fc.post(f"/workspaces/{WS}/kqlDatabases", {
            "displayName": N["kqlDatabase"],
            "creationPayload": {"databaseType": "ReadWrite", "parentEventhouseItemId": eh["id"]},
        })
        if r.status_code == 202:
            fc.poll_lro(r)
        kql = fc.get_item_by_name(WS, N["kqlDatabase"], "KQLDatabase")
    if kql:
        results["kqlDatabase"] = kql["id"]
    # Try to run the seed KQL against the eventhouse query URI
    try:
        ehg = fc.get(f"/workspaces/{WS}/eventhouses/{eh['id']}").json()
        quri = ehg.get("properties", {}).get("queryServiceUri")
        if quri:
            run_kql(quri, N["kqlDatabase"], (ROOT / "eventhouse" / "setup.kql").read_text())
            print("    KQL seed executed")
        else:
            print("    ! queryServiceUri not ready; run eventhouse/setup.kql manually")
    except Exception as e:
        print(f"    ! KQL seed deferred ({e}); run eventhouse/setup.kql in the KQL queryset")


def run_kql(query_uri: str, database: str, script: str):
    import requests
    token = get_token("https://kusto.fabric.microsoft.com")
    # split into individual control commands (.create / .set-or-append) on blank-line boundaries
    cmds, buf = [], []
    for line in script.splitlines():
        s = line.strip()
        if s.startswith("//") or not s:
            if buf:
                cmds.append("\n".join(buf)); buf = []
            continue
        buf.append(line)
    if buf:
        cmds.append("\n".join(buf))
    for cmd in cmds:
        if not cmd.strip().startswith("."):
            continue
        resp = requests.post(
            f"{query_uri}/v1/rest/mgmt",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            data=json.dumps({"db": database, "csl": cmd}),
            timeout=120,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"KQL '{cmd[:40]}...' -> {resp.status_code} {resp.text[:200]}")


def phase_semantic(fc: FabricClient, lakehouse_id: str):
    print("\n[4] Semantic model (Direct Lake on SQL endpoint)")
    # Resolve the lakehouse SQL analytics endpoint (Direct Lake on SQL uses SSO,
    # so the report can read data without a separately-bound cloud credential).
    lh = fc.get(f"/workspaces/{WS}/lakehouses/{lakehouse_id}").json()
    sep = lh.get("properties", {}).get("sqlEndpointProperties", {})
    sql_conn, sql_id = sep.get("connectionString"), sep.get("id")
    if not sql_conn or not sql_id:
        raise RuntimeError("SQL analytics endpoint not provisioned yet; retry shortly")
    repl = {"{{WORKSPACE_ID}}": WS, "{{LAKEHOUSE_ID}}": lakehouse_id,
            "{{SQL_ENDPOINT}}": sql_conn, "{{SQL_ENDPOINT_ID}}": sql_id}
    sm_dir = ROOT / "semantic_model"
    parts = []
    for f in sorted(sm_dir.rglob("*")):
        if f.is_file() and f.name != ".platform":
            text = f.read_text(encoding="utf-8")
            for k, v in repl.items():
                text = text.replace(k, v)
            parts.append(part(f.relative_to(sm_dir).as_posix(), text))
    definition = {"parts": parts}
    # Update in place when it already exists so the report stays bound to the id.
    existing = fc.get_item_by_name(WS, N["semanticModel"], "SemanticModel")
    if existing:
        print(f"  ~ updating existing SemanticModel definition ({existing['id']})")
        fc.update_item_definition(WS, existing["id"], definition)
        results["semanticModel"] = existing["id"]
        return existing["id"]
    sm = fc.create_item(WS, N["semanticModel"], "SemanticModel", definition=definition,
                        description="Retail Direct Lake model")
    results["semanticModel"] = sm["id"]
    return sm["id"]


def phase_report(fc: FabricClient, semantic_model_id: str):
    print("\n[5] Power BI report")
    rep_dir = ROOT / "report"
    report_builder.build(semantic_model_id, rep_dir)
    definition = definition_from_folder(rep_dir)
    rep = fc.create_item(WS, N["report"], "Report", definition=definition,
                        description="Retail demo report")
    results["report"] = rep["id"]
    return rep["id"]


def phase_activator(fc: FabricClient):
    print("\n[6] Activator / Reflex")
    reflex_json = (ROOT / "activator" / "reflex_definition.json").read_text()
    definition = {"parts": [part("ReflexEntities.json", reflex_json)]}
    try:
        rx = fc.create_item(WS, N["activator"], "Reflex", definition=definition,
                            description="Low-inventory trigger")
        results["activator"] = rx["id"]
    except Exception as e:
        print(f"    ! definition rejected ({str(e)[:120]}); creating empty Reflex item")
        try:
            rx = fc.create_item(WS, N["activator"], "Reflex",
                                description="Low-inventory trigger (configure rule in UI)")
            results["activator"] = rx["id"]
            print("    -> open the Activator and add rule: OnHand < 15 => notify")
        except Exception as e2:
            print(f"    ! Activator skipped ({str(e2)[:120]}). See activator/reflex_definition.json")


def phase_ontology(fc: FabricClient, lakehouse_id: str):
    """Build + apply the full Fabric IQ ontology (entities, properties, data
    bindings, relationships). Updates in place if the item already exists."""
    import ontology_builder
    definition = ontology_builder.build(WS, lakehouse_id)
    existing = fc.get_item_by_name(WS, "RetailOntology", "Ontology")
    if existing:
        print(f"    ~ updating ontology definition ({existing['id']}) "
              f"-> {len(definition['parts'])} parts")
        try:
            fc.update_item_definition(WS, existing["id"], definition)
            results["ontology"] = existing["id"]
            return
        except Exception as e:
            print(f"    ! update failed ({str(e)[:120]}); recreating")
            fc.delete(f"/workspaces/{WS}/items/{existing['id']}")
    on = fc.create_item(WS, "RetailOntology", "Ontology", definition=definition,
                        description="Retail Fabric IQ ontology", skip_if_exists=False)
    results["ontology"] = on["id"]


def phase_preview(fc: FabricClient, lakehouse_id: str):
    print("\n[7-9] Fabric IQ items")
    # Ontology: fully built + data-bound from ontology_builder
    try:
        phase_ontology(fc, lakehouse_id)
        print("    Ontology   : entities + relationships authored + data-bound")
    except Exception as e:
        print(f"    ! Ontology build failed: {str(e)[:160]}")
    # Agents remain guided (preview SDK / no public REST)
    print("    Data Agent : run agents/data_agent_setup.py in a Fabric notebook")
    print("    Ops Agent  : configure from agents/operations_agent_config.yaml")


PHASES = ["lakehouse", "data", "eventhouse", "semantic", "report", "activator", "preview"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", default="", help="comma list of phases to skip")
    ap.add_argument("--only", default="", help="comma list: run only these phases")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    def run(p):
        return (p in only) if only else (p not in skip)

    print(f"Deploying Retail Fabric IQ demo -> workspace {N.get('lakehouse')} @ {WS}")
    fc = FabricClient()

    lakehouse_id = results.get("lakehouse")
    semantic_id = results.get("semanticModel")
    if run("lakehouse"):
        lakehouse_id = phase_lakehouse(fc)
    else:
        it = fc.get_item_by_name(WS, N["lakehouse"], "Lakehouse"); lakehouse_id = it and it["id"]
    if run("data") and lakehouse_id:
        phase_data(fc, lakehouse_id)
    if run("eventhouse"):
        phase_eventhouse(fc)
    if run("semantic") and lakehouse_id:
        semantic_id = phase_semantic(fc, lakehouse_id)
    else:
        it = fc.get_item_by_name(WS, N["semanticModel"], "SemanticModel"); semantic_id = it and it["id"]
    if run("report") and semantic_id:
        phase_report(fc, semantic_id)
    if run("activator"):
        phase_activator(fc)
    if run("preview"):
        phase_preview(fc, lakehouse_id)

    out = ROOT / "deploy_output.json"
    out.write_text(json.dumps(results, indent=2))
    print("\nDone. Item IDs:")
    print(json.dumps(results, indent=2))
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()
