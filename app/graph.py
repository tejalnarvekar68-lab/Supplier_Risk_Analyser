
from typing import TypedDict
import json

from langgraph.graph import StateGraph, START, END
from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    Supplier,
    EvidenceDocument,
    StructuredRecord,
    Assessment
)

from app.research import search_company

from app.risk_engine import (
    financial_risk,
    delivery_risk,
    assess_risk
)
from app.financial_evidence import get_financial_retriever, extract_financial_facts, calculate_financial_row


# --------------------------------------------------
# 1. LANGGRAPH STATE
# --------------------------------------------------

class State(TypedDict, total=False):

    supplier_id: int
    include_external_research: bool

    supplier: dict

    documents: list[dict]
    financial_chunks: list[dict]
    financial_facts: dict
    document_financial_row: dict
    document_ratio_provenance: dict
    financial_records: list[dict]
    shipment_records: list[dict]

    research: list[dict]

    financial_result: dict
    delivery_result: dict
    evidence_review_result: dict

    result: dict
    assessment_id: int


# --------------------------------------------------
# 2. PREPARE NODE
# Fetch supplier information from SQL database
# --------------------------------------------------

def prepare(state):

    with SessionLocal() as db:

        # Fetch supplier
        s = db.get(Supplier, state["supplier_id"])

        if s is None:
            raise ValueError("Supplier not found")

        # Fetch supplier documents
        docs = db.scalars(
            select(EvidenceDocument).where(
                EvidenceDocument.supplier_id == s.id
            )
        ).all()

        # Fetch structured records
        records = db.scalars(
            select(StructuredRecord)
            .where(StructuredRecord.supplier_id == s.id)
            .order_by(StructuredRecord.id)
        ).all()

        financial_chunks = []
        document_summaries = []
        for d in docs:
            raw = d.extracted_text or ""
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                payload = {}
            chunks = payload.get("financial_chunks", []) if isinstance(payload, dict) else []
            for chunk in chunks:
                chunk.setdefault("company", s.name)
                chunk.setdefault("document", d.filename)
                chunk.setdefault("document_id", d.id)
            financial_chunks.extend(chunks)
            document_summaries.append({
                "id": d.id, "filename": d.filename, "sha256": d.sha256,
                "financial_chunks": len(chunks),
                "legacy_raw_text": not bool(chunks)
            })

        return {
            "supplier": {
                "id": s.id,
                "name": s.name,
                "country": s.country,
                "category": s.category,
                "website": s.website
            },

            "documents": document_summaries,
            "financial_chunks": financial_chunks,

            "financial_records": [
                r.data for r in records
                if r.kind == "financial"
            ],

            "shipment_records": [
                r.data for r in records
                if r.kind == "shipment"
            ]
        }


# --------------------------------------------------
# 3. RESEARCH NODE
# Fetch external company information
# --------------------------------------------------

def research_node(state):

    s = state["supplier"]

    research_results = (
        search_company(s["name"], s.get("country"))
        if state.get("include_external_research")
        else []
    )

    return {
        "research": research_results
    }


# --------------------------------------------------
# 4. ANALYSIS NODE
# Calculate financial and delivery risk
# --------------------------------------------------

def analyse(state):

    # Structured CSV remains supported. Document-derived facts are extracted
    # through hybrid retrieval and merged only when CSV does not supply a value.
    financial_row = dict(
        state["financial_records"][-1] if state["financial_records"] else {}
    )
    facts, document_row, provenance = {}, {}, {}
    chunks = state.get("financial_chunks", [])
    if chunks:
        retriever = get_financial_retriever(chunks)
        queries = [
            "current assets current liabilities current ratio liquidity",
            "total debt total borrowings total assets debt to assets leverage",
            "operating cash flow cash generated from operations",
            "net income profit after tax loss",
        ]
        retrieved_by_id = {}
        for query in queries:
            for item in retriever.search(query, company=state["supplier"]["name"], top_k=5):
                retrieved_by_id[item["id"]] = item
        retrieved = list(retrieved_by_id.values())
        facts = extract_financial_facts(retrieved, state["supplier"]["name"])
        document_row, provenance = calculate_financial_row(facts)
        # Explicit structured records take precedence over document-derived facts.
        for key, value in document_row.items():
            if financial_row.get(key) in (None, ""):
                financial_row[key] = value

    shipment_row = (
        state["shipment_records"][-1]
        if state["shipment_records"]
        else {}
    )

    financial_result = financial_risk(financial_row)
    financial_result["document_extraction"] = {
        "facts": facts,
        "ratio_provenance": provenance,
        "document_derived_values": document_row,
    }
    delivery_result = delivery_risk(shipment_row)

    return {
        "financial_result": financial_result,
        "delivery_result": delivery_result,
        "financial_facts": facts,
        "document_financial_row": document_row,
        "document_ratio_provenance": provenance,
    }


# --------------------------------------------------
# 5. EVIDENCE REVIEW NODE
# Prepare evidence-review result
# --------------------------------------------------

def evidence_review_node(state):

    documents = state.get("documents", [])
    research = state.get("research", [])

    # IMPORTANT:
    # This is a temporary unavailable result.
    # Connect your RAG/LLM evidence analysis here.
    # Do not assign an arbitrary risk score just because
    # documents or research are present.

    if documents or research:

        evidence_review_result = {
            "score": None,
            "available": False,
            "reasons": [],
            "limitations": [
                "Evidence is available, but RAG/LLM risk analysis "
                "has not yet been connected."
            ]
        }

    else:

        evidence_review_result = {
            "score": None,
            "available": False,
            "reasons": [],
            "limitations": [
                "No documents or external research available."
            ]
        }

    return {
        "evidence_review_result": evidence_review_result
    }


# --------------------------------------------------
# 6. SCORING NODE
# Calculate overall supplier risk
# --------------------------------------------------

def score(state):

    result = assess_risk(
        financial=state["financial_result"],
        delivery=state["delivery_result"],
        evidence_review=state["evidence_review_result"]
    )

    return {
        "result": result
    }


# --------------------------------------------------
# 7. VALIDATE NODE
# Check and prepare final assessment
# --------------------------------------------------

def validate(state):

    result = state["result"]

    # Validate risk score
    if result["risk_score"] is not None:

        if not 0 <= result["risk_score"] <= 100:
            raise ValueError("Score out of range")

    # Attach supplier and supporting information
    result.update({

        "supplier": state["supplier"],

        "evidence": state["documents"],

        "external_research": state["research"],

        "financial_analysis": state["financial_result"],

        "delivery_analysis": state["delivery_result"],

        "evidence_review_analysis": state["evidence_review_result"]

    })

    return {
        "result": result
    }


# --------------------------------------------------
# 8. PERSIST NODE
# Save assessment in SQL database
# --------------------------------------------------

def persist(state):

    r = state["result"]

    with SessionLocal() as db:

        a = Assessment(

            supplier_id=state["supplier_id"],

            risk_score=(
                r["risk_score"]
                if r["risk_score"] is not None
                else 0
            ),

            risk_band=r["risk_band"],

            coverage_pct=r["coverage_pct"],

            result=r
        )

        db.add(a)

        db.commit()

        db.refresh(a)

        return {
            "assessment_id": a.id
        }


# --------------------------------------------------
# 9. BUILD LANGGRAPH WORKFLOW
# --------------------------------------------------

def build_graph():

    g = StateGraph(State)

    # Register nodes
    for name, fn in [
        ("prepare", prepare),
        ("research", research_node),
        ("analyse", analyse),
        ("evidence_review", evidence_review_node),
        ("score", score),
        ("validate", validate),
        ("persist", persist)
    ]:

        g.add_node(name, fn)

    # Connect nodes
    g.add_edge(START, "prepare")

    g.add_edge("prepare", "research")

    g.add_edge("research", "analyse")

    g.add_edge("analyse", "evidence_review")

    g.add_edge("evidence_review", "score")

    g.add_edge("score", "validate")

    g.add_edge("validate", "persist")

    g.add_edge("persist", END)

    return g.compile()


# Compile graph
supplier_risk_graph = build_graph()


# --------------------------------------------------
# 10. RUN ASSESSMENT
# Entry point called by FastAPI
# --------------------------------------------------

def run_assessment(
    supplier_id: int,
    include_external_research: bool = False
):

    state = supplier_risk_graph.invoke({

        "supplier_id": supplier_id,

        "include_external_research": include_external_research

    })

    return {
        **state["result"],
        "assessment_id": state["assessment_id"]
    }
