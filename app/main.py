import logging
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db
from app.models import Supplier, EvidenceDocument, StructuredRecord, Assessment
from app.schemas import SupplierCreate, SupplierOut, AssessmentRequest
from app.security import require_api_key
from app.evidence import parse_structured_csv, sha256_bytes
from app.financial_evidence import build_financial_chunks
import json
import mimetypes
from app.graph import run_assessment
from statistics import mean

logging.basicConfig(level=getattr(logging,settings.log_level.upper(),logging.INFO))
logger=logging.getLogger(__name__)
Base.metadata.create_all(bind=engine)  # Bootstrap only; use Alembic migrations in production.
app=FastAPI(title="Supplier Risk Analyser API",version="0.1.0")

'''
@app.get & @app.post are HTTP methds of FastAPI which tells them how to communicate with clients

@app.get - retrieves info from server
@app.post - Sends info to server

'''

@app.get("/")
def root():
    return {
        "message": "Welcome to Supplier Risk Analyser API",
        "version": "0.1.0",
        "docs": "/docs"
    }

@app.get("/health")
def health():
    return {"status":"ok"}



@app.get("/ready")
def ready(db:Session=Depends(get_db)):
    try: db.execute(select(1)); return {"status":"ready"}
    except Exception as exc: raise HTTPException(503,"Database unavailable") from exc


#This code creates a new supplier record in the database through a FastAPI endpoint using SQLAlchemy.
#A frontend sends a POST request to /suppliers to register a new supplier.
@app.post("/suppliers",response_model=SupplierOut,dependencies=[Depends(require_api_key)])
#FastAPI validates this incoming data (Incoming supplier information, validated using the Pydantic SupplierCreate schema) and makes it available through payload.
def create_supplier(payload:SupplierCreate,db:Session=Depends(get_db)):
    #Here, Supplier is the SQLAlchemy model class defined in models.py.
    s=Supplier(name=payload.name.strip(),
               country=payload.country,
               category=payload.category,
               website=str(payload.website) if payload.website else None)
    #At this stage, s is only a Python object. It has not yet been saved in the database.
    '''
    SQLAlchemy:
    - db.add(s) adds the new supplier in Supplier database.
    - db.commit() saves it.
    - db.refresh(s) retrieves database-generated values, such as its ID.
    '''
    db.add(s); db.commit(); db.refresh(s); return s



#retrieves all suppliers from the Database.
@app.get("/suppliers",response_model=list[SupplierOut],dependencies=[Depends(require_api_key)])
def list_suppliers(db:Session=Depends(get_db)):
    #The scalars call runs the literal SQL Syntaxes within it
    '''
    SELECT * FROM suppliers
    ORDER BY name
    '''
    return db.scalars(select(Supplier).order_by(Supplier.name)).all()




#Basically tells FastAPI to get http://localhost:8000/suppliers/5 instead of http://localhost:8000 (Default opening of FasTAPI)
#response_model - Pydantic Output
#IMP - Here the supplier_id came from app.py (user request)

#retrieves supplier based on ID from the database.
#This {supplier_id} is given by user on UI
@app.get("/suppliers/{supplier_id}",response_model=SupplierOut,dependencies=[Depends(require_api_key)])
def get_supplier(supplier_id:int,db:Session=Depends(get_db)):

    #This is a SQLAlchemy method that retrieves a record using its primary key.
    s=db.get(Supplier,supplier_id)

    if not s:
        raise HTTPException(404,"Supplier not found")
    return s



#This endpoint allows a user to upload a document (PDF, TXT, CSV, etc.) against a specific supplier in your Supplier Risk Analyser.
#IMP - "/suppliers/{supplier_id}/documents" This is a customised URL that I am giving
@app.post("/suppliers/{supplier_id}/documents",dependencies=[Depends(require_api_key)])
#UploadFile gives a place on UI to upload files
async def upload_document(supplier_id:int,file:UploadFile=File(...),db:Session=Depends(get_db)):
    #Error 1
    if not db.get(Supplier,supplier_id):
        raise HTTPException(404,"Supplier not found")

    #This line reads the uploaded file and stores its contents in a Python variable called content
    #settings.max_upload_mb*1024*1024+1 - calculates the maximum permitted file size in bytes. 1024 are bytes
    content=await file.read(settings.max_upload_mb*1024*1024+1)

    #Error 2
    if len(content)>settings.max_upload_mb*1024*1024:
        raise HTTPException(413,"File too large")
    #accesses the name part of the uploaded file, suppose if someone puts up the path link then only name is extracted
    #If no file is uploaded then "upload" is passed as default
    filename=Path(file.filename or "upload").name

    # Extract only finance-related paragraphs/tables and preserve provenance.
    try:
        supplier = db.get(Supplier, supplier_id)
        chunks = build_financial_chunks(filename, content, company=supplier.name)
        mime = mimetypes.guess_type(filename)[0] or file.content_type or "application/octet-stream"
        # Store structured financial chunks in the existing column. This avoids
        # a database migration; graph.py understands this JSON representation.
        text = json.dumps({"schema_version": 2, "financial_chunks": chunks}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("Document financial extraction failed")
        raise HTTPException(400, str(exc)) from exc

    digest=sha256_bytes(content)

    #Checks if this Supplier already exists based on the digest
    existing=db.scalar(select(EvidenceDocument).where(EvidenceDocument.supplier_id==supplier_id,EvidenceDocument.sha256==digest))
    if existing:
        return {"id":existing.id,"duplicate":True,"filename":existing.filename}
    d=EvidenceDocument(supplier_id=supplier_id,filename=filename,content_type=mime,sha256=digest,extracted_text=text)
    db.add(d); db.commit(); db.refresh(d)

    #Whatever is returned here returns to UI for Visibility
    return {"id":d.id,"filename":filename,"sha256":digest,
            "financial_chunks_extracted":len(chunks),
            "stored_representation":"financial_chunks_json",
            "chunks_extracted_from_doc":chunks}



#This endpoint allows you to upload structured CSV data (financial or shipment data) for a particular supplier in your Supplier Risk Analyser.
#IMP - To upload this structured data refer to the files already available in the data folder
@app.post("/suppliers/{supplier_id}/data",dependencies=[Depends(require_api_key)])
async def upload_data(supplier_id:int,kind:str=Query(...,pattern="^(financial|shipment)$"),file:UploadFile=File(...),db:Session=Depends(get_db)):

    #Error 1
    if not db.get(Supplier,supplier_id):
        raise HTTPException(404,"Supplier not found")

    #Error 2
    filename=Path(file.filename or "upload").name
    if Path(filename).suffix.lower()!=".csv":
        raise HTTPException(400,"Structured metrics must be CSV")

    #Error 3
    content=await file.read(settings.max_upload_mb*1024*1024+1)
    if len(content)>settings.max_upload_mb*1024*1024:
        raise HTTPException(413,"File too large")

    #Error 4
    try: rows=parse_structured_csv(content)
    except Exception as exc:
        raise HTTPException(400,"Could not parse CSV") from exc

    #Error 5
    if not rows:
        raise HTTPException(400,"CSV has no rows")

    db.add(StructuredRecord(supplier_id=supplier_id,kind=kind,filename=filename,data=rows[-1])); db.commit()

    return {"stored_rows":len(rows),"used_for_current_score":"last row","kind":kind}



#This endpoint triggers the supplier risk assessment workflow in your Supplier Risk Analyser.
@app.post("/assessments",dependencies=[Depends(require_api_key)])
def create_assessment(payload:AssessmentRequest,db:Session=Depends(get_db)):

    #Error 1
    if not db.get(Supplier,payload.supplier_id):
        raise HTTPException(404,"Supplier not found")

    #Error 2
    try:
        return run_assessment(payload.supplier_id,payload.include_external_research)
    except Exception as exc:
        logger.exception("Assessment failed")
        raise HTTPException(500,"Assessment failed; inspect server logs") from exc



#This endpoint retrieves the assessment history of a particular supplier.
# In simple terms, it shows all the risk assessments previously conducted for that supplier, with the latest assessment appearing first.
@app.get("/suppliers/{supplier_id}/assessments",dependencies=[Depends(require_api_key)])
def history(supplier_id:int,db:Session=Depends(get_db)):
    if not db.get(Supplier,supplier_id):
        raise HTTPException(404,"Supplier not found")
    rows=db.scalars(select(Assessment).where(Assessment.supplier_id==supplier_id).order_by(Assessment.created_at.desc())).all()
    return [{"id":a.id,"supplier_id":a.supplier_id,"risk_score":a.risk_score,"risk_band":a.risk_band,
             "coverage_pct":a.coverage_pct,"result":a.result,"created_at":a.created_at.isoformat()} for a in rows]


#Yes! The database generates it automatically. You do not need to write a separate command to generate the ID.
#But this happens because your model defines the id column as a primary key.


#Extracts the assessment
@app.get("/assessments/{assessment_id}",dependencies=[Depends(require_api_key)])
def assessment_detail(assessment_id:int,db:Session=Depends(get_db)):
    a=db.get(Assessment,assessment_id)

    if not a:
        raise HTTPException(404,"Assessment not found")

    return {"id":a.id,"supplier_id":a.supplier_id,"risk_score":a.risk_score,"risk_band":a.risk_band,
            "coverage_pct":a.coverage_pct,"result":a.result,"created_at":a.created_at.isoformat()}


#Comparing 2 suppliers from RAG Pipeline
@app.get("/suppliers/{supplier_id1}/{supplier_id2}/RAG",dependencies=[Depends(require_api_key)])
def supplier_comparison(supplier_id1:int,supplier_id2:int,db:Session=Depends(get_db)):
    #VVIMP - only get's the Primary Key, need to write another query to get other columns
    if not db.get(Supplier,supplier_id1):
        raise HTTPException(404,f"Supplier {supplier_id1} not found in Database")

    elif not db.get(Supplier,supplier_id2):
            raise HTTPException(404,f"Supplier {supplier_id2} not found in Database")

    #I want to Flag error only if all 3 data inputs are not available for that supplier

    uploaded_doc1 = db.scalars(select(EvidenceDocument).where(EvidenceDocument.supplier_id==supplier_id1).order_by(EvidenceDocument.created_at.desc())).all()
    structured_doc1 = db.scalars(select(StructuredRecord).where(StructuredRecord.supplier_id==supplier_id1).order_by(StructuredRecord.created_at.desc())).all()
    assessment_doc1 = db.scalars(select(Assessment).where(Assessment.supplier_id==supplier_id1).order_by(Assessment.created_at.desc())).all()

    if not uploaded_doc1 and not structured_doc1 and not assessment_doc1:
        raise HTTPException(404,f"Supplier {supplier_id1} has no documents uploaded for it!")

    uploaded_doc2 = db.scalars(select(EvidenceDocument).where(EvidenceDocument.supplier_id==supplier_id2).order_by(EvidenceDocument.created_at.desc())).all()
    structured_doc2 = db.scalars(select(StructuredRecord).where(StructuredRecord.supplier_id==supplier_id2).order_by(StructuredRecord.created_at.desc())).all()
    assessment_doc2 = db.scalars(select(Assessment).where(Assessment.supplier_id==supplier_id2).order_by(Assessment.created_at.desc())).all()

    if not uploaded_doc2 and not structured_doc2 and not assessment_doc2:
        raise HTTPException(404,f"Supplier {supplier_id2} has no documents uploaded for it!")


    #this is the right way to retrieve a record when supplier_id is not the primary key of the Assessment table [Just don't use .get()]

    documents_supplier1 = db.scalars(select(EvidenceDocument).where(EvidenceDocument.supplier_id==supplier_id1).order_by(EvidenceDocument.created_at.desc())).all()
    doc_supplier1 = [doc.extracted_text for doc in documents_supplier1 if doc.extracted_text is not None]
    single_doc_supplier1 = "\n\n".join(text for text in doc_supplier1 if text)

    documents_supplier2 = db.scalars(select(EvidenceDocument).where(EvidenceDocument.supplier_id==supplier_id2).order_by(EvidenceDocument.created_at.desc())).all()
    doc_supplier2 = [doc.extracted_text for doc in documents_supplier2 if doc.extracted_text is not None]
    single_doc_supplier2 = "\n\n".join(text for text in doc_supplier2 if text)

    combined_supplier_doc = single_doc_supplier1 + "\n" + single_doc_supplier2



    str_documents_supplier1 = db.scalars(select(StructuredRecord).where(StructuredRecord.supplier_id==supplier_id1).order_by(StructuredRecord.created_at.desc())).all()
    str_doc_supplier1 = [str_doc.data for str_doc in str_documents_supplier1 if str_doc.data is not None]
    single_str_doc_supplier1 = str_doc_supplier1 if str_doc_supplier1 else ""


    str_documents_supplier2 = db.scalars(select(StructuredRecord).where(StructuredRecord.supplier_id==supplier_id2).order_by(StructuredRecord.created_at.desc())).all()
    str_doc_supplier2 = [str_doc.data for str_doc in str_documents_supplier2 if str_doc.data is not None]
    single_str_doc_supplier2 = str_doc_supplier2 if str_doc_supplier2 else ""

    combined_str_supplier_doc = single_str_doc_supplier1 + single_str_doc_supplier2

    #VVIMP - db.scalar takes only one record the first one coming up
    #I will take average of all the risk scores
    #VVIMP point - db.scalar returns only one result(first matching object); need to put scalars
    risk_scores_supplier1 = db.scalars(select(Assessment).where(Assessment.supplier_id==supplier_id1).order_by(Assessment.created_at.desc())).all()
    scores_supplier1 = [score.risk_score for score in risk_scores_supplier1 if score is not None]
    Final_risk_supplier1 = mean(scores_supplier1) if scores_supplier1 else None


    risk_scores_supplier2 = db.scalars(select(Assessment).where(Assessment.supplier_id==supplier_id2).order_by(Assessment.created_at.desc())).all()
    scores_supplier2 = [score.risk_score for score in risk_scores_supplier2 if score is not None]
    Final_risk_supplier2 = mean(scores_supplier2) if scores_supplier2 else None





    from app.RAG_chain import rag_chain_analysis

    retriever_analysis = rag_chain_analysis(supplier_id1=supplier_id1,
                                            supplier_id2=supplier_id2,
                                            combined_supplier_doc=combined_supplier_doc,
                                            combined_str_supplier_doc=combined_str_supplier_doc,
                                            Final_risk_supplier1=Final_risk_supplier1,
                                            Final_risk_supplier2=Final_risk_supplier2)

    return retriever_analysis