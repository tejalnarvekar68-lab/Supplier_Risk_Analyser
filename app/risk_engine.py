
from statistics import mean


def num(value):
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------
# 1. FINANCIAL RISK
# --------------------------------------------------

def financial_risk(row: dict) -> dict:
    scores, reasons, limitations = [], [], []

    # Parameter 1: Current Ratio
    ca = num(row.get("current_assets"))
    cl = num(row.get("current_liabilities"))
    reported_current_ratio = num(row.get("current_ratio"))

    if reported_current_ratio is not None and reported_current_ratio >= 0:
        ratio = reported_current_ratio
        score = 20 if ratio >= 1.5 else 45 if ratio >= 1 else 80
        scores.append(score)
        reasons.append({"metric": "current_ratio", "value": round(ratio, 3), "score": score, "source": "reported_ratio"})
    elif ca is not None and cl is not None and cl > 0:
        ratio = ca / cl
        score = 20 if ratio >= 1.5 else 45 if ratio >= 1 else 80

        scores.append(score)
        reasons.append({
            "metric": "current_ratio",
            "value": round(ratio, 3),
            "score": score
        })
    else:
        limitations.append("Current ratio could not be calculated.")

    # Parameter 2: Debt to Assets
    debt = num(row.get("total_debt"))
    assets = num(row.get("total_assets"))
    reported_debt_to_assets = num(row.get("debt_to_assets"))

    if reported_debt_to_assets is not None and reported_debt_to_assets >= 0:
        ratio = reported_debt_to_assets
        score = 20 if ratio <= 0.3 else 45 if ratio <= 0.6 else 80
        scores.append(score)
        reasons.append({"metric": "debt_to_assets", "value": round(ratio, 3), "score": score, "source": "reported_ratio"})
    elif debt is not None and assets is not None and assets > 0:
        ratio = debt / assets
        score = 20 if ratio <= 0.3 else 45 if ratio <= 0.6 else 80

        scores.append(score)
        reasons.append({
            "metric": "debt_to_assets",
            "value": round(ratio, 3),
            "score": score
        })
    else:
        limitations.append("Debt-to-assets ratio could not be calculated.")

    # Parameter 3: Operating Cash Flow
    ocf = num(row.get("operating_cash_flow"))

    if ocf is not None:
        score = 25 if ocf >= 0 else 85

        scores.append(score)
        reasons.append({
            "metric": "operating_cash_flow",
            "value": ocf,
            "score": score
        })
    else:
        limitations.append("Operating cash flow data is missing.")

    # Parameter 4: Net Income
    income = num(row.get("net_income"))

    if income is not None:
        score = 25 if income >= 0 else 75

        scores.append(score)
        reasons.append({
            "metric": "net_income",
            "value": income,
            "score": score
        })
    else:
        limitations.append("Net income data is missing.")

    return {
        "score": round(mean(scores), 1) if scores else None,
        "available": bool(scores),
        "reasons": reasons,
        "limitations": limitations
    }


# --------------------------------------------------
# 2. DELIVERY RISK
# --------------------------------------------------

def delivery_risk(row: dict) -> dict:
    total = num(row.get("shipments_total"))

    if total is None or total <= 0:
        return {
            "score": None,
            "available": False,
            "reasons": [],
            "limitations": ["No usable shipment counts supplied."]
        }

    scores, reasons, limitations = [], [], []

    # Higher values are better for these metrics.
    high_good_metrics = [
        (
            "shipments_on_time",
            "on_time_rate",
            [(0.98, 15), (0.95, 35), (0.90, 60)]
        ),
        (
            "shipments_first_attempt_success",
            "first_attempt_success_rate",
            [(0.97, 15), (0.93, 35), (0.85, 60)]
        )
    ]

    for field, label, thresholds in high_good_metrics:
        value = num(row.get(field))

        if value is not None:
            if value < 0 or value > total:
                limitations.append(
                    f"Invalid shipment count for {field}."
                )
                continue

            rate = value / total

            score = next(
                (s for threshold, s in thresholds if rate >= threshold),
                85
            )

            scores.append(score)
            reasons.append({
                "metric": label,
                "value": round(rate, 4),
                "score": score
            })

    # Lower values are better for these metrics.
    low_good_metrics = [
        (
            "shipments_failed",
            "failed_shipment_rate",
            [(0.01, 15), (0.03, 35), (0.08, 60)]
        ),
        (
            "shipments_damaged",
            "damaged_shipment_rate",
            [(0.002, 15), (0.01, 35), (0.03, 60)]
        )
    ]

    for field, label, thresholds in low_good_metrics:
        value = num(row.get(field))

        if value is not None:
            if value < 0 or value > total:
                limitations.append(
                    f"Invalid shipment count for {field}."
                )
                continue

            rate = value / total

            score = next(
                (s for threshold, s in thresholds if rate <= threshold),
                85
            )

            scores.append(score)
            reasons.append({
                "metric": label,
                "value": round(rate, 4),
                "score": score
            })

    if not scores:
        limitations.append("No usable shipment metrics supplied.")

    return {
        "score": round(mean(scores), 1) if scores else None,
        "available": bool(scores),
        "reasons": reasons,
        "limitations": limitations
    }


# --------------------------------------------------
# 3. OVERALL SUPPLIER RISK ASSESSMENT
# --------------------------------------------------

def assess_risk(financial, delivery, evidence_review):

    domains = []

    # Financial risk: 40%
    if financial["available"]:
        domains.append({
            "name": "financial",
            "score": financial["score"],
            "weight": 0.4
        })

    # Delivery risk: 40%
    if delivery["available"]:
        domains.append({
            "name": "delivery",
            "score": delivery["score"],
            "weight": 0.4
        })

    # Evidence review risk: 20%
    if evidence_review["available"]:
        domains.append({
            "name": "evidence_review",
            "score": evidence_review["score"],
            "weight": 0.2
        })

    # Calculate overall risk score
    if domains:

        total_weight = sum(
            domain["weight"] for domain in domains
        )

        score = round(
            sum(
                domain["score"] * domain["weight"]
                for domain in domains
            ) / total_weight,
            1
        )

        # Assign risk category
        band = (
            "Low" if score < 25
            else "Moderate" if score < 50
            else "High" if score < 75
            else "Critical"
        )

    else:
        score = None
        band = "Insufficient data"

    # Calculate domain coverage
    coverage_pct = round(
        (len(domains) / 3) * 100,
        1
    )

    # Identify missing risk areas
    missing_data = [
        name
        for name, available in [
            ("financial", financial["available"]),
            ("delivery", delivery["available"]),
            ("evidence_review", evidence_review["available"])
        ]
        if not available
    ]

    # Final result
    return {
        "risk_score": score,
        "risk_band": band,
        "coverage_pct": coverage_pct,

        "domains": {
            domain["name"]: {
                "score": domain["score"],
                "weight": domain["weight"]
            }
            for domain in domains
        },

        "missing_data": missing_data,

        "interpretation": (
            "Higher score indicates more observed risk. "
            "Missing evidence is not low risk."
        ),

        "policy_note": (
            "Illustrative weights and thresholds. Validate with "
            "procurement, finance, legal and risk owners."
        )
    }
