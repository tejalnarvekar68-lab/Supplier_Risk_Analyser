"""Financial evidence extraction and hybrid retrieval for supplier risk analysis.

Pipeline: parse -> financial chunk detection -> dense/BM25 retrieval -> rerank
-> LLM fact extraction -> deterministic ratio calculation.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

import fitz
import pdfplumber
import numpy as np
import pandas as pd
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer
from openai import OpenAI
import pdfplumber
from app.config import settings

logger = logging.getLogger(__name__)

FINANCIAL_TERMS = re.compile(
    r"\b(financial statements?|income statement|balance sheet|cash flows?|"
    r"revenue|turnover|profit|loss|assets|liabilities|equity|borrowings?|debt|"
    r"receivables?|payables?|inventory|working capital|impairment|provisions?|"
    r"contingent liabilities|going concern|liquidity|solvency|covenant|"
    r"capital expenditure|operating expenses?|interest expense|ebitda|ebit|"
    r"current ratio|quick ratio|debt.?to.?equity|debt.?to.?assets|"
    r"interest coverage|cash ratio|net worth|net income|operating cash flow)\b",
    re.I,
)
SECTION_TERMS = re.compile(
    r"(consolidated financial statements?|statement of (?:profit or loss|"
    r"comprehensive income|financial position|cash flows?)|balance sheet|"
    r"income statement|notes to the (?:financial )?statements?|"
    r"management discussion|financial review|risk management)", re.I,
)

EMBEDDING_MODEL = getattr(settings, "financial_embedding_model", "sentence-transformers/all-MiniLM-L6-v2")
RERANKER_MODEL = getattr(settings, "financial_reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
FINANCIAL_INDEX_DIR = Path(getattr(settings, "financial_index_dir", "./financial_rag_data"))


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def _split_paragraphs(text: str, max_chars: int = 2200) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(paragraph[i:i + max_chars] for i in range(0, len(paragraph), max_chars))
        elif len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip()
        else:
            if current:
                chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def _table_to_text(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [[str(cell or "").strip() for cell in row] + [""] * (width - len(row)) for row in rows]
    return "TABLE\n" + "\n".join(" | ".join(row) for row in normalized)


def _extract_pages(filename: str, content: bytes) -> list[dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    digest = hashlib.sha256(content).hexdigest()
    pages: list[dict[str, Any]] = []
    if ext == ".pdf":
        with fitz.open(stream=content, filetype="pdf") as pdf, pdfplumber.open(io.BytesIO(content)) as plumber:
            for i, page in enumerate(pdf):
                tables = []
                if i < len(plumber.pages):
                    try:
                        for table in plumber.pages[i].extract_tables() or []:
                            rows = [[str(cell or "").strip() for cell in row] for row in table if row]
                            rows = [row for row in rows if any(row)]
                            if len(rows) >= 2:
                                tables.append(rows)
                    except Exception:
                        logger.exception("PDF table extraction failed on page %s", i + 1)
                pages.append({"page": i + 1, "text": page.get_text("text") or "", "tables": tables, "document": filename, "document_sha256": digest})
    elif ext == ".docx":
        from docx import Document
        doc = Document(io.BytesIO(content))
        text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        tables = [[cell.text for cell in row.cells] for table in doc.tables for row in table.rows]
        pages.append({"page": 1, "text": text, "tables": [tables] if tables else [], "document": filename, "document_sha256": digest})
    elif ext in {".xlsx", ".xls"}:
        workbook = pd.read_excel(io.BytesIO(content), sheet_name=None, nrows=5000)
        for sheet, frame in workbook.items():
            text = f"Sheet: {sheet}\n" + frame.fillna("").to_string(index=False)
            rows = [frame.columns.astype(str).tolist()] + frame.fillna("").astype(str).values.tolist()
            pages.append({"page": sheet, "text": text, "tables": [rows], "document": filename, "document_sha256": digest})
    elif ext == ".csv":
        frame = pd.read_csv(io.BytesIO(content), nrows=5000)
        rows = [frame.columns.astype(str).tolist()] + frame.fillna("").astype(str).values.tolist()
        pages.append({"page": 1, "text": frame.fillna("").to_string(index=False), "tables": [rows], "document": filename, "document_sha256": digest})
    elif ext in {".txt", ".md"}:
        pages.append({"page": 1, "text": content.decode("utf-8", errors="replace"), "tables": [], "document": filename, "document_sha256": digest})
    else:
        raise ValueError("Supported formats: PDF, DOCX, XLSX, XLS, CSV, TXT, MD")
    return pages


def build_financial_chunks(filename: str, content: bytes, company: str, fiscal_year: int | None = None) -> list[dict[str, Any]]:
    """Keep only finance-related paragraphs/tables, preserving source metadata."""
    output = []
    for page in _extract_pages(filename, content):
        text = page["text"]
        if not (FINANCIAL_TERMS.search(text) or page["tables"]):
            continue
        heading = SECTION_TERMS.search(text[:1800])
        section = heading.group(0) if heading else "Financial disclosure (section inferred)"
        for index, chunk in enumerate(_split_paragraphs(text)):
            if FINANCIAL_TERMS.search(chunk):
                output.append({
                    "id": f"{page['document_sha256'][:10]}-p{page['page']}-t{index}",
                    "text": chunk, "company": company,
                    "financial_year": fiscal_year, "section": section,
                    "page": page["page"], "content_type": "narrative",
                    "document": filename, "document_sha256": page["document_sha256"],
                })
        for table_index, table in enumerate(page["tables"]):
            table_text = _table_to_text(table)
            if FINANCIAL_TERMS.search(table_text):
                output.append({
                    "id": f"{page['document_sha256'][:10]}-p{page['page']}-table{table_index}",
                    "text": table_text, "company": company,
                    "financial_year": fiscal_year, "section": section,
                    "page": page["page"], "content_type": "financial_table",
                    "document": filename, "document_sha256": page["document_sha256"],
                })
    return output


_MODEL_CACHE: dict[str, Any] = {}
_RETRIEVER_CACHE: dict[str, "FinancialRetriever"] = {}


class FinancialRetriever:
    """Dense + BM25 reciprocal-rank fusion, metadata filtering, cross-encoder reranking."""
    def __init__(self, chunks: list[dict[str, Any]]):
        self.chunks = chunks
        if not chunks:
            self.embedder = self.reranker = self.vectors = self.bm25 = None
            return
        if EMBEDDING_MODEL not in _MODEL_CACHE:
            _MODEL_CACHE[EMBEDDING_MODEL] = SentenceTransformer(EMBEDDING_MODEL)
        if RERANKER_MODEL not in _MODEL_CACHE:
            _MODEL_CACHE[RERANKER_MODEL] = CrossEncoder(RERANKER_MODEL)
        self.embedder = _MODEL_CACHE[EMBEDDING_MODEL]
        self.reranker = _MODEL_CACHE[RERANKER_MODEL]
        self.vectors = self.embedder.encode([c["text"] for c in chunks], normalize_embeddings=True, show_progress_bar=False)
        self.bm25 = BM25Okapi([_tokenize(c["text"]) for c in chunks])

    def search(self, query: str, company: str | None = None, fiscal_year: int | None = None,
               top_k: int = 5, candidate_k: int = 20) -> list[dict[str, Any]]:
        if not self.chunks:
            return []
        eligible = [i for i, c in enumerate(self.chunks)
                    if (company is None or str(c.get("company", "")).lower() == company.lower())
                    and (fiscal_year is None or c.get("financial_year") in (None, fiscal_year))]
        if not eligible:
            return []
        qv = self.embedder.encode([query], normalize_embeddings=True)[0]
        dense_scores = self.vectors @ qv
        sparse_scores = self.bm25.get_scores(_tokenize(query))
        dense_rank = sorted(eligible, key=lambda i: dense_scores[i], reverse=True)
        sparse_rank = sorted(eligible, key=lambda i: sparse_scores[i], reverse=True)
        rrf: dict[int, float] = {}
        for ranking in (dense_rank[:candidate_k], sparse_rank[:candidate_k]):
            for rank, idx in enumerate(ranking, start=1):
                rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rank)
        candidates = sorted(rrf, key=rrf.get, reverse=True)[:candidate_k]
        rerank_scores = self.reranker.predict([(query, self.chunks[i]["text"]) for i in candidates])
        ranked = sorted(zip(candidates, rerank_scores), key=lambda x: float(x[1]), reverse=True)[:top_k]
        return [{**self.chunks[i], "rerank_score": float(score)} for i, score in ranked]


def get_financial_retriever(chunks: list[dict[str, Any]]) -> FinancialRetriever:
    """Reuse the in-process index when the exact evidence set is unchanged."""
    fingerprint = hashlib.sha256(
        json.dumps(chunks, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    if fingerprint not in _RETRIEVER_CACHE:
        _RETRIEVER_CACHE[fingerprint] = FinancialRetriever(chunks)
        # Bound memory in long-running API workers.
        if len(_RETRIEVER_CACHE) > 8:
            _RETRIEVER_CACHE.pop(next(iter(_RETRIEVER_CACHE)))
    return _RETRIEVER_CACHE[fingerprint]


def _llm_client() -> OpenAI:
    key = settings.openrouter_api_key
    if not key or key.startswith("replace-with"):
        raise RuntimeError("Set OPENROUTER_API_KEY in the environment before financial fact extraction")
    return OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")


def extract_financial_facts(retrieved_chunks: list[dict[str, Any]], company: str) -> dict[str, Any]:
    """Extract reported ratios and raw metric facts; never ask the LLM to do arithmetic."""
    if not retrieved_chunks:
        return {"metrics": {}, "reported_ratios": {}, "sources": [], "limitations": ["No relevant financial evidence retrieved."]}
    evidence = "\n\n".join(
        f"[SOURCE {i}] document={c.get('document')} page={c.get('page')} section={c.get('section')}\n{c['text']}"
        for i, c in enumerate(retrieved_chunks, 1)
    )
    prompt = f'''You extract auditable financial facts for supplier risk analysis. Company: {company}.
Use ONLY the evidence below. Do not infer missing values and do not calculate ratios.
Return valid JSON with this exact shape:
{{"metrics":{{"current_assets":{{"value":null,"unit":null,"period":null,"source":"SOURCE 1"}},
"current_liabilities":{{"value":null,"unit":null,"period":null,"source":"SOURCE 1"}},
"total_debt":{{"value":null,"unit":null,"period":null,"source":"SOURCE 1"}},
"total_assets":{{"value":null,"unit":null,"period":null,"source":"SOURCE 1"}},
"operating_cash_flow":{{"value":null,"unit":null,"period":null,"source":"SOURCE 1"}},
"net_income":{{"value":null,"unit":null,"period":null,"source":"SOURCE 1"}}}},
"reported_ratios":{{"current_ratio":{{"value":null,"unit":null,"period":null,"source":"SOURCE 1"}},
"debt_to_assets":{{"value":null,"unit":null,"period":null,"source":"SOURCE 1"}}}},
"limitations":[]}}
Only include metrics/ratios actually stated. Numeric values must be numbers; preserve units and periods. For reported ratios, do not mistake percentages or amounts for ratios. If evidence is insufficient, omit that field. Evidence:\n{evidence}'''
    response = _llm_client().chat.completions.create(
        model=getattr(settings, "financial_extraction_model", "openai/gpt-4o-mini"),
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    data = json.loads(response.choices[0].message.content or "{}")
    data.setdefault("metrics", {})
    data.setdefault("reported_ratios", {})
    data["sources"] = [{k: c.get(k) for k in ("document", "page", "section", "id", "rerank_score")} for c in retrieved_chunks]
    data.setdefault("limitations", [])
    return data


def _same_period_and_unit(a: dict, b: dict) -> bool:
    # Avoid combining figures from different reporting periods or currencies/scales.
    if a.get("period") and b.get("period") and a["period"] != b["period"]:
        return False
    if a.get("unit") and b.get("unit") and a["unit"].lower() != b["unit"].lower():
        return False
    return True


def calculate_financial_row(facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    metrics = facts.get("metrics", {})
    reported = facts.get("reported_ratios", {})
    row: dict[str, Any] = {}
    provenance: dict[str, Any] = {"reported_ratios": {}, "calculated_ratios": {}}
    for name in ("operating_cash_flow", "net_income"):
        item = metrics.get(name)
        if isinstance(item, dict) and isinstance(item.get("value"), (int, float)):
            row[name] = item["value"]
            provenance[name] = item
    ratio_specs = {
        "current_ratio": ("current_assets", "current_liabilities", lambda n, d: n / d),
        "debt_to_assets": ("total_debt", "total_assets", lambda n, d: n / d),
    }
    for ratio, (numerator_key, denominator_key, operation) in ratio_specs.items():
        existing = reported.get(ratio)
        if isinstance(existing, dict) and isinstance(existing.get("value"), (int, float)):
            # Convert reported percentages to decimal fractions for scoring.
            reported_value = float(existing["value"])
            if ratio == "debt_to_assets" and str(existing.get("unit", "")).strip() in {"%", "percent", "percentage"}:
                reported_value /= 100.0
            row[ratio] = reported_value
            provenance["reported_ratios"][ratio] = {**existing, "normalized_value": reported_value}
            continue
        numerator, denominator = metrics.get(numerator_key), metrics.get(denominator_key)
        if (isinstance(numerator, dict) and isinstance(denominator, dict)
                and isinstance(numerator.get("value"), (int, float))
                and isinstance(denominator.get("value"), (int, float))
                and _same_period_and_unit(numerator, denominator)
                and float(denominator["value"]) > 0):
            value = operation(float(numerator["value"]), float(denominator["value"]))
            row[ratio] = value
            provenance["calculated_ratios"][ratio] = {
                "numerator": numerator, "denominator": denominator,
                "formula": f"{numerator_key} / {denominator_key}", "value": value,
            }
    return row, provenance
