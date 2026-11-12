import os, requests, pandas as pd, streamlit as st

st.set_page_config(page_title="Supplier Risk Analyser",layout="wide")
st.title("Supplier Risk Analyser")
st.caption("Evidence-backed supplier assessment • LangGraph workflow")
api=st.sidebar.text_input("API URL",os.getenv("API_URL","http://127.0.0.1:8000"))
key=st.sidebar.text_input("API key",os.getenv("APP_API_KEY",""),type="password")
headers={"X-API-Key":key}

def get(path):
    r=requests.get(api.rstrip("/")+path,headers=headers,timeout=30); r.raise_for_status(); return r.json()
def post(path,**kwargs):
    r=requests.post(api.rstrip("/")+path,headers=headers,timeout=120,**kwargs); r.raise_for_status(); return r.json()

try: suppliers=get("/suppliers")
except Exception as e: st.error(f"API connection failed: {e}"); st.stop()

t1,t2,t3=st.tabs(["Overview","Add supplier","Supplier workspace"])
with t1:
    st.subheader("Supplier register")
    st.metric("Suppliers",len(suppliers))
    if suppliers: st.dataframe(pd.DataFrame(suppliers),use_container_width=True,hide_index=True)
with t2:
    with st.form("new_supplier"):
        name=st.text_input("Supplier name"); country=st.text_input("Country","India")
        category=st.text_input("Procurement category"); website=st.text_input("Website (optional)")
        submit=st.form_submit_button("Create supplier")
    if submit:
        payload={"name":name,"country":country or None,"category":category or None}
        if website.strip(): payload["website"]=website.strip()
        '''
        try: st.success(f"Created supplier ID {post('/suppliers',json=payload)['id']}")
        except Exception as e: st.error(str(e))'''

        try:
        created = post("/suppliers", json=payload)

        st.success(f"Created supplier ID {created['id']}")

        # Reload the dashboard and fetch the updated supplier list
        st.rerun()

        except Exception as e:
            st.error(str(e))



with t3:
    if not suppliers: st.info("Add a supplier first.")
    else:
        options={f"{x['name']} (ID {x['id']})":x["id"] for x in suppliers}
        sid=options[st.selectbox("Supplier",list(options))]
        doc=st.file_uploader("Evidence document (PDF/TXT/CSV/XLSX)",type=["pdf","txt","csv","xlsx"])
        if st.button("Upload document",disabled=doc is None):
            try: st.success(str(post(f"/suppliers/{sid}/documents",files={"file":(doc.name,doc.getvalue(),doc.type or "application/octet-stream")})))
            except Exception as e: st.error(str(e))
        kind=st.selectbox("Structured data type",["financial","shipment"])
        csv=st.file_uploader("Metrics CSV",type=["csv"])
        if st.button("Upload metrics",disabled=csv is None):
            try: st.success(str(post(f"/suppliers/{sid}/data",params={"kind":kind},files={"file":(csv.name,csv.getvalue(),"text/csv")})))
            except Exception as e: st.error(str(e))
        research=st.checkbox("Include external research (requires Tavily key on API)")
        if st.button("Run risk assessment",type="primary"):
            try: st.session_state["assessment"]=post("/assessments",json={"supplier_id":sid,"include_external_research":research})
            except Exception as e: st.error(str(e))
        result=st.session_state.get("assessment")
        if result:
            a,b,c=st.columns(3)
            a.metric("Risk score","Insufficient data" if result["risk_score"] is None else f"{result['risk_score']}/100")
            b.metric("Risk band",result["risk_band"]); c.metric("Evidence coverage",f"{result['coverage_pct']}%")
            st.warning(result["interpretation"]); st.json(result)
        if st.button("Load assessment history"):
            try: st.session_state["history"]=get(f"/suppliers/{sid}/assessments")
            except Exception as e: st.error(str(e))
        if st.session_state.get("history"):
            st.dataframe(pd.DataFrame([{k:x[k] for k in ["id","created_at","risk_score","risk_band","coverage_pct"]} for x in st.session_state["history"]]),use_container_width=True)
