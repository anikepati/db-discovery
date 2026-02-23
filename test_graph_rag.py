"""
test_graph_rag.py — Validate GraphRAG retrieval quality.
Shows graph structure, retrieval scores, and accuracy.
"""

import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from graph_rag import SchemaGraphRAG

DB_PATH = "sample.db"

EXPECTED = {
    "customer email":         ("customers", "email"),
    "customer first name":    ("customers", "first_name"),
    "customer last name":     ("customers", "last_name"),
    "customer phone number":  ("customers", "phone"),
    "product sku code":       ("products", "sku"),
    "order quantity":         ("order_items", "quantity"),
    "order status":           ("orders", "status"),
    "product stock level":    ("products", "stock_quantity"),
}


def main():
    rag = SchemaGraphRAG(DB_PATH).build(use_cache=False)

    # --- Graph Stats ---
    stats = rag.get_graph_stats()
    print("=" * 75)
    print("KNOWLEDGE GRAPH")
    print("=" * 75)
    print(f"  Nodes: {stats['total_nodes']}  |  Edges: {stats['total_edges']}")
    print(f"  Node types: {json.dumps(stats['node_types'])}")
    print(f"  Edge types: {json.dumps(stats['edge_types'])}")

    # --- Schema ---
    print(f"\n{'=' * 75}")
    print("SCHEMA (from graph)")
    print("=" * 75)
    print(rag.get_schema_context_text())

    # --- Load attributes ---
    with open("data_attributes.json") as f:
        attrs = json.load(f)["attributes"]

    # --- Retrieval ---
    print("=" * 75)
    print("GRAPHRAG RETRIEVAL")
    print("=" * 75)

    correct = 0
    total = 0

    for attr in attrs:
        name, value, context = attr["name"], str(attr["value"]), attr["context"]
        results = rag.retrieve(name, value=value, context=context, top_k=3)

        print(f"\n{'─' * 75}")
        print(f"  Attribute:  {name}")
        print(f"  Value:      {value}")
        print(f"  Context:    {context}")

        for i, r in enumerate(results):
            marker = "→" if i == 0 else " "
            graph_ctx = ""
            if r["graph_context"]["foreign_keys"]:
                fks = [f"{fk.get('direction','')}: {fk.get('target', fk.get('source',''))}"
                       for fk in r["graph_context"]["foreign_keys"]]
                graph_ctx = f"  FKs={fks}"
            print(
                f"    {marker} {r['table']}.{r['column']:20} "
                f"combined={r['score_combined']:.3f} "
                f"(emb={r['score_embedding']:.2f} graph={r['score_graph']:.2f} val={r['score_value']:.2f}) "
                f"concepts={r['concepts']} patterns={r['value_patterns']}{graph_ctx}"
            )

        # Check accuracy
        if name in EXPECTED:
            total += 1
            exp_t, exp_c = EXPECTED[name]
            if results and results[0]["table"] == exp_t and results[0]["column"] == exp_c:
                correct += 1
                print(f"    ✅ CORRECT")
            else:
                got = f"{results[0]['table']}.{results[0]['column']}" if results else "NONE"
                print(f"    ❌ WRONG — expected {exp_t}.{exp_c}, got {got}")
        else:
            print(f"    ⚠️  No expected mapping (should be UNMAPPED)")

    print(f"\n{'=' * 75}")
    print(f"ACCURACY: {correct}/{total} ({100*correct/total:.0f}%)")
    print("=" * 75)


if __name__ == "__main__":
    main()
