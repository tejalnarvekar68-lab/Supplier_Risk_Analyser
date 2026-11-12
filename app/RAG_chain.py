
# ==========================================
# SUPPLIER RISK ANALYSER - RAG CHAIN
# ==========================================

# 1. Imports

import json
import uuid

from operator import itemgetter

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams
from langchain_qdrant import QdrantVectorStore

from app.config import settings
#from langchain_openrouter import ChatOpenRouter


# ==========================================
# 2. Convert retrieved Documents into text
# ==========================================

def format_docs(docs):

    if not docs:
        return "No relevant documents were retrieved."

    return "\n\n".join(
        f"Source metadata: {doc.metadata}\n"
        f"Document content: {doc.page_content}"
        for doc in docs
    )


# ==========================================
# 3. Main RAG function
# ==========================================

def rag_chain_analysis(
    supplier_id1: int | None = None,
    supplier_id2: int | None = None,
    combined_supplier_doc: str | None = None,
    combined_str_supplier_doc: list[dict] | None = None,
    Final_risk_supplier1: float | None = None,
    Final_risk_supplier2: float | None = None
):

    supplier1 = supplier_id1
    supplier2 = supplier_id2

    # --------------------------------------
    # STEP 1: Prepare documents
    # --------------------------------------

    combined_docs = []

    # Uploaded documents
    if combined_supplier_doc:

        uploaded_doc = Document(
            page_content=combined_supplier_doc,
            metadata={
                "supplier_ids": [supplier1, supplier2],
                "source_type": "uploaded_document"
            }
        )

        combined_docs.append(uploaded_doc)

    # Structured documents
    if combined_str_supplier_doc:

        for row in combined_str_supplier_doc:

            if not isinstance(row, dict):
                continue

            structured_doc = Document(
                page_content=json.dumps(row, default=str),
                metadata={
                    "supplier_id": row.get("supplier_id"),
                    "supplier": row.get("supplier", "Unknown"),
                    "source_type": "structured_record"
                }
            )

            combined_docs.append(structured_doc)

    # No evidence available
    if not combined_docs:

        if Final_risk_supplier1 is None and Final_risk_supplier2 is None:
            return "No data available for supplier comparison."

    # --------------------------------------
    # STEP 2: Split documents into chunks
    # --------------------------------------

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50
    )

    splitting_docs = splitter.split_documents(combined_docs)

    # --------------------------------------
    # STEP 3: Setup embeddings
    # --------------------------------------

    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        api_key=settings.openrouter_api_key
    )

    # --------------------------------------
    # STEP 4: Connect to local Qdrant
    # --------------------------------------

    client = QdrantClient(
        path="./qdrant_data"
    )

    # Separate collection for each supplier pair
    collection_name = (
        f"supplier_comparison_{supplier1}_{supplier2}"
    )

    # Create collection if it does not exist
    if not client.collection_exists(collection_name):

        vector_size = len(
            embeddings.embed_query("Test embedding")
        )

        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE
            )
        )

    # Connect LangChain to Qdrant
    vectorstore = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings
    )

    # --------------------------------------
    # STEP 5: Insert documents into Qdrant
    # --------------------------------------

    if splitting_docs:

        # Deterministic IDs prevent duplicate insertion
        # of unchanged chunks during repeated comparisons.

        document_ids = []

        for index, doc in enumerate(splitting_docs):

            unique_text = (
                f"{doc.metadata}|{doc.page_content}|{index}"
            )

            document_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_DNS,
                    unique_text
                )
            )

            document_ids.append(document_id)

        vectorstore.add_documents(
            documents=splitting_docs,
            ids=document_ids
        )

    # --------------------------------------
    # STEP 6: Create retriever
    # --------------------------------------

    retriever = vectorstore.as_retriever(
        search_kwargs={"k": 6}
    )

    # --------------------------------------
    # STEP 7: Setup LLM
    # --------------------------------------

    llm = ChatOpenAI(
        model="openrouter/free",
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
        temperature=0
    )

    # --------------------------------------
    # STEP 8: Create system instructions
    # --------------------------------------

    system_prompt = """
    You are a Senior Procurement Risk Analyst specialising in:

    - Supplier evaluation
    - Financial risk assessment
    - Supply-chain reliability
    - Strategic sourcing
    - Supplier delivery performance

    Your responsibility is to compare TWO suppliers and determine
    which supplier presents the LOWER OVERALL PROCUREMENT RISK.

    You must use the following information:

    1. Retrieved supplier documents.
    2. Structured supplier records.
    3. Calculated supplier risk scores.

    Risk scores:

    Supplier {sup1}: {risk_score1}
    Supplier {sup2}: {risk_score2}

    Apply the following evaluation principles:

    - Give 50% weight to the calculated risk scores.
    - Give 50% weight to the evidence found in documents.
    - Explain the evidence supporting your conclusion.
    - Identify financial and delivery-related risks.
    - Distinguish facts from assumptions.
    - Do not invent missing information.

    If there is insufficient information to compare the suppliers,
    clearly state which information is missing.

    Do not assume that a missing risk score means zero risk.

    Return your answer with these sections:

    1. Supplier comparison
    2. Financial risk comparison
    3. Delivery and operational risk comparison
    4. Supporting evidence
    5. Final recommendation
    """

    # --------------------------------------
    # STEP 9: Create prompt
    # --------------------------------------

    prompt = ChatPromptTemplate.from_messages([

        ("system", system_prompt),

        ("human", """
        Compare Supplier {sup1} and Supplier {sup2}.

        Retrieved supplier evidence:

        {context}

        Supplier 1 calculated risk score: {risk_score1}

        Supplier 2 calculated risk score: {risk_score2}

        Which supplier is safer to select for procurement?

        Explain your reasoning using the available evidence.
        """)
    ])

    # --------------------------------------
    # STEP 10: Connect Retriever + Prompt + LLM
    # --------------------------------------

    rag_chain = (

        {
            "context": (
                itemgetter("question")
                | retriever
                | RunnableLambda(format_docs)
            ),

            "sup1": itemgetter("sup1"),

            "sup2": itemgetter("sup2"),

            "risk_score1": itemgetter("risk_score1"),

            "risk_score2": itemgetter("risk_score2")
        }

        | prompt

        | llm

        | StrOutputParser()
    )

    # --------------------------------------
    # STEP 11: Run the RAG chain
    # --------------------------------------

    question = (
        f"Compare supplier {supplier1} and supplier {supplier2}. "
        "Evaluate their financial and delivery risks and identify "
        "which supplier is safer to select."
    )

    result = rag_chain.invoke({

        "question": question,

        "sup1": supplier1,

        "sup2": supplier2,

        "risk_score1": Final_risk_supplier1,

        "risk_score2": Final_risk_supplier2
    })

    # --------------------------------------
    # STEP 12: Return result
    # --------------------------------------

    client.close()

    return result
