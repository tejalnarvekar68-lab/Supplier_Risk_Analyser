from app.risk_engine import financial_risk, delivery_risk, assess_risk

def test_current_ratio_low_risk():
    x=financial_risk({"current_assets":200,"current_liabilities":100})
    assert x["available"] and x["score"]==20

def test_missing_financial_data_not_low_risk():
    x=financial_risk({})
    assert not x["available"] and x["score"] is None

def test_delivery_metrics():
    x=delivery_risk({"shipments_total":100,"shipments_on_time":96,"shipments_failed":2})
    assert x["available"] and len(x["reasons"])==2

def test_no_data_is_insufficient():
    x=assess_risk(financial_risk({}),delivery_risk({}),[],[])
    assert x["risk_score"] is None and x["risk_band"]=="Insufficient data"
