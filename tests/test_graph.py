from app.graph import supplier_risk_graph

def test_graph_nodes():
    nodes=supplier_risk_graph.get_graph().nodes
    for name in ["prepare","research","analyse","score","validate","persist"]:
        assert name in nodes
