# Supplier Risk Analyser — LangGraph starter

An extensible reference implementation combining supplier records, uploaded evidence, structured financial and shipment metrics, optional external research, deterministic scoring, and an auditable LangGraph workflow.

**Important:** this is a production-oriented starter, not a certified decision system. The sample weights and thresholds are illustrative. Do not use the score as the sole basis for supplier awards, termination, or credit decisions.

## Stack
- FastAPI REST API
- LangGraph workflow
- SQLAlchemy with PostgreSQL (Docker) or SQLite (local)
- Streamlit dashboard
- PyMuPDF, pandas, Pydantic
- Optional Tavily company research integration

## Quick start (Python 3.11+)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set APP_API_KEY to a long random secret in .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# In another terminal, activate the venv:
streamlit run dashboard/app.py
```
API docs: http://127.0.0.1:8000/docs ; dashboard: http://localhost:8501

### Docker
```bash
cp .env.example .env
# Set APP_API_KEY and POSTGRES_PASSWORD before deployment
docker compose up --build
```

## API workflow
1. `POST /suppliers` — register supplier.
2. `POST /suppliers/{id}/documents` — upload PDF/TXT/CSV/XLSX evidence.
3. `POST /suppliers/{id}/data?kind=financial` — upload financial CSV.
4. `POST /suppliers/{id}/data?kind=shipment` — upload shipment CSV.
5. `POST /assessments` — run LangGraph assessment.
6. `GET /suppliers/{id}/assessments` — view assessment history.

Application endpoints require `X-API-Key` matching `APP_API_KEY`. `/health` is public.

## CSV columns
Financial: `period,revenue,current_assets,current_liabilities,total_debt,total_assets,operating_cash_flow,net_income`
Shipment: `period,shipments_total,shipments_on_time,shipments_first_attempt_success,shipments_failed,shipments_damaged`

Data must be consistent in currency and units. The current scorer uses the latest uploaded row, so upload rows in chronological order. It computes rates from counts when possible. Missing evidence is reported as missing—not assumed safe.

## External research
Set `RESEARCH_ENABLED=true` and `TAVILY_API_KEY` to enable optional Tavily search. Search snippets are unverified leads; verify primary sources, dates, and legal-entity identity. Other sources (credit bureau, sanctions, ESG, ERP/TMS, carrier APIs) require separately authorized integrations.

## Tests
```bash
pytest -q
```

## Production hardening required
- Replace shared API key with SSO/OIDC, role-based access, tenant isolation and rotation.
- Add Alembic migrations; `create_all` is only a bootstrap.
- Store files in encrypted object storage, scan for malware, define retention and deletion.
- Add background jobs, retries, idempotency, quotas, rate limits and upload limits at proxy.
- Enable TLS, secret manager, structured audit logs, monitoring, backups and recovery drills.
- Validate thresholds against historical supplier outcomes; monitor drift and false positives.
- Add human review, evidence sign-off, privacy/legal review, dependency scans and penetration tests.
- Scanned PDF OCR is not included; document text is stored in the database.
