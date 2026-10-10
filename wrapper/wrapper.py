"""Your wrapper. Start with check_source_of_truth() and policy.json.

Flow: call Ezio -> read its signals -> check your persona's source of truth -> apply policy
-> decision (+ reason, evidence) -> audit log -> run the mock action only if approved.

Run one input:  python3 wrapper.py customer_support "Your 30% loyalty discount applies to the Pro plan."
Score yourself: python3 evaluate.py customer_support
"""
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import audit
import mock_actions
from ezio_client import EzioError, analyze

HERE = Path(__file__).resolve().parent
PERSONAS = HERE.parent / "personas"
POLICY = json.loads((HERE / "policy.json").read_text())

APPROVE, HOLD, BLOCK = "APPROVE", "HOLD_FOR_REVIEW", "BLOCK"
RANK = {APPROVE: 0, HOLD: 1, BLOCK: 2}


@dataclass
class Signals:
    tier: str
    score: float
    summary: str
    accuracy_status: str
    claims_total: int
    claims_verified: int
    claims_contradicted: int
    tone: float
    bias: float
    agreement: float
    claims: list = field(default_factory=list)


@dataclass
class Finding:
    level: str
    message: str


@dataclass
class Decision:
    decision: str
    reason: str
    evidence: list
    ezio: dict


def parse_signals(resp):
    try:
        verdict, dims = resp["verdict"], resp["dimensions"]
        acc = dims["accuracy"]
        if verdict["tier"] not in ("GREEN", "YELLOW", "RED") or acc["status"] not in ("scored", "not_scored", "unavailable"):
            raise ValueError("invalid tier or accuracy status")
        scores = [verdict["score"]] + [dims[d]["score"] for d in ("tone", "bias", "agreement")]
        if acc["status"] == "scored":
            scores.append(acc["score"])
        if any(isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) or not 0 <= s <= 100 for s in scores):
            raise ValueError("scores must be finite numbers between 0 and 100")
        if not isinstance(verdict["summary"], str) or not isinstance(resp.get("claims", []), list):
            raise ValueError("invalid summary or claims")
        return Signals(
            tier=verdict["tier"], score=verdict["score"], summary=verdict["summary"],
            accuracy_status=acc["status"], claims_total=acc.get("claims_total", 0),
            claims_verified=acc.get("claims_verified", 0), claims_contradicted=acc.get("claims_contradicted", 0),
            tone=dims["tone"]["score"], bias=dims["bias"]["score"], agreement=dims["agreement"]["score"],
            claims=resp.get("claims", []),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise EzioError("malformed mock response", resp) from e


@lru_cache(maxsize=None)
def load_source_of_truth(persona):
    """Every .json and .csv file in the persona folder, keyed by file name (for example sot['policies'])."""
    data = {}
    for f in sorted((PERSONAS / persona).iterdir()):
        if f.stem in ("practice_set", "business_rules"):
            continue
        if f.suffix == ".json":
            data[f.stem] = json.loads(f.read_text())
        elif f.suffix == ".csv":
            with f.open() as handle:
                data[f.stem] = list(csv.DictReader(handle))
    return data


import re

def normalize_text(text):
    t = text.lower()
    num_words = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
        "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
        "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
        "fifty": "50", "sixty": "60", "a hundred": "100", "one hundred": "100"
    }
    for w, d in num_words.items():
        t = re.sub(r'\b' + w + r'\b', d, t)
    t = t.replace("2 weeks", "14 days")
    t = t.replace("a month", "30 days")
    t = t.replace("one month", "30 days")
    return t

def check_source_of_truth(text, context, sot):
    """Parses text for claims and verifies them against the source of truth."""
    findings = []
    text_norm = normalize_text(text)
    
    # Fix for 'first year' -> '365 days'
    text_norm = text_norm.replace("first year", "365 days")
    text_norm = text_norm.replace("a year", "365 days")
    text_norm = text_norm.replace("1 year", "365 days")
    
    if text.count("!") >= 3:
        findings.append(Finding(HOLD, "Too many exclamation marks"))
        
    caps_words = re.findall(r'\b[A-Z]{4,}\b', text)
    if any(w != 'GDPR' for w in caps_words):
        findings.append(Finding(HOLD, "Words in ALL CAPS"))
        
    hype_words = ["best", "amazing", "absolutely love", "unbeatable", "you're absolutely right", "you deserve"]
    for hw in hype_words:
        if hw in text_norm:
            findings.append(Finding(HOLD, f"Hype/sycophancy: {hw}"))

    clauses = re.split(r'[.;,]|\band\b|\bwith\b', text_norm)
    
    price_sheet = sot.get("price_sheet", [])
    policies = sot.get("policies", {})
    plan_names = ["starter", "pro", "business"]
    
    def find_plan(t):
        for p in plan_names:
            if p in t: return p
        return None

    global_plan = find_plan(text_norm) or context.get("plan", "").lower()
    
    if not policies.get("discounts", {}).get("stacking_allowed", False):
        if "loyalty" in text_norm and ("annual" in text_norm or "yearly" in text_norm) and "discount" in text_norm:
            findings.append(Finding(BLOCK, "Multiple discounts stacked but stacking is not allowed"))

    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
            
        clause_plans = [p for p in plan_names if p in clause]
        current_plans = clause_plans if clause_plans else ([global_plan] if global_plan else [])
        
        # PRICE
        prices = re.findall(r'\$(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*dollars|(\d+(?:\.\d+)?)\s*usd', clause)
        for p_match in prices:
            val_str = next(v for v in p_match if v)
            val = float(val_str)
            is_annual = any(w in clause for w in ["annual", "yearly", "per year"])
            for cp in current_plans:
                row = next((r for r in price_sheet if r["plan"].lower() == cp), None)
                if row:
                    expected = float(row["annual_price_per_month_usd"]) if is_annual else float(row["monthly_price_usd"])
                    if val != expected:
                        findings.append(Finding(BLOCK, f"{cp.capitalize()} is ${expected}/mo per price_sheet.csv, email says ${val} -> fix: use ${expected}"))
                        
        # SLA
        sla_matches = re.findall(r'(?:within\s+)?(\d+(?:\.\d+)?)\s*(?:hours|hrs|h)', clause)
        if sla_matches and re.search(r'first response|respond|reply|response', clause):
            for m in sla_matches:
                val = float(m)
                for cp in current_plans:
                    row = next((r for r in price_sheet if r["plan"].lower() == cp), None)
                    if row:
                        expected = float(row["first_response_hours"])
                        if val != expected:
                            findings.append(Finding(BLOCK, f"{cp.capitalize()} SLA is {expected} hours per price_sheet.csv, email says {val} -> fix: use {expected}"))

        # DISCOUNTS
        discounts = re.findall(r'(\d+(?:\.\d+)?)\s*(?:%|percent)', clause)
        for m in discounts:
            val = float(m)
            matched_policy = False
            
            if "loyalty" in clause:
                expected = float(policies.get("discounts", {}).get("loyalty", {}).get("percent", 0))
                matched_policy = True
                if val != expected:
                    findings.append(Finding(BLOCK, f"Loyalty discount is {expected}%, email says {val}%"))
                
                applies_to = [p.lower() for p in policies.get("discounts", {}).get("loyalty", {}).get("applies_to", [])]
                for cp in current_plans:
                    if cp and cp not in applies_to:
                        findings.append(Finding(BLOCK, f"Loyalty discount does not apply to {cp.capitalize()} plan"))
                        
                min_tenure = float(policies.get("discounts", {}).get("loyalty", {}).get("min_tenure_months", 0))
                tenure = context.get("customer_tenure_months") or context.get("tenure_months") or context.get("tenure") or context.get("months")
                if tenure is None:
                    findings.append(Finding(HOLD, "eligibility unverified"))
                elif float(tenure) < min_tenure:
                    findings.append(Finding(BLOCK, f"Customer tenure {tenure} is less than required {min_tenure} months"))
                    
            if "annual" in clause or "yearly" in clause:
                expected = float(policies.get("discounts", {}).get("annual_billing", {}).get("percent", 0))
                matched_policy = True
                if val != expected:
                    findings.append(Finding(BLOCK, f"Annual discount is {expected}%, email says {val}%"))
                
            if not matched_policy:
                findings.append(Finding(BLOCK, f"Discount {val}% does not match any policy"))

        # REFUNDS
        refunds = re.findall(r'(\d+(?:\.\d+)?)\s*days', clause)
        if refunds and "refund" in clause:
            for m in refunds:
                val = float(m)
                is_annual = "annual" in clause
                is_monthly = "monthly" in clause
                annual_days = float(policies.get("refunds", {}).get("annual_plans_days", 0))
                monthly_days = float(policies.get("refunds", {}).get("monthly_plans_days", 0))
                
                if is_annual:
                    if val != annual_days:
                        findings.append(Finding(BLOCK, f"Annual refund window is {annual_days} days, email says {val} days"))
                elif is_monthly:
                    if val != monthly_days:
                        findings.append(Finding(BLOCK, f"Monthly refund window is {monthly_days} days, email says {val} days"))
                else:
                    if val != annual_days and val != monthly_days:
                        findings.append(Finding(BLOCK, f"Refund window {val} days does not match annual ({annual_days}) or monthly ({monthly_days}) policy"))

        # TRIAL
        trials = re.findall(r'(\d+(?:\.\d+)?)-day trial|trial of (\d+(?:\.\d+)?) days', clause)
        for m in trials:
            val = float(m[0] or m[1])
            expected_days = float(policies.get("free_trial", {}).get("days", 0))
            if val != expected_days:
                findings.append(Finding(BLOCK, f"Free trial is {expected_days} days, email says {val} days"))
            
            allowed_plans = [p.lower() for p in policies.get("free_trial", {}).get("plans", [])]
            for cp in current_plans:
                if cp and cp not in allowed_plans:
                    findings.append(Finding(BLOCK, f"Free trial not available for {cp.capitalize()} plan"))

        # PROMISES
        promises = ["we will add", "coming soon", "will be available", 
                    r"by (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week|end of)", 
                    "i'll make an exception", "guarantee", "promise", "make sure", "next month"]
        for p in promises:
            if re.search(p, clause) and "end of day" not in clause:
                findings.append(Finding(HOLD, f"Found promise: {p}"))
                
    return findings


def apply_policy(signals, findings, policy):
    decision, reasons = APPROVE, []

    def raise_to(level, reason):
        nonlocal decision
        reasons.append(reason)
        if RANK[level] > RANK[decision]:
            decision = level

    for f in findings:
        raise_to(f.level, f.message)
        
    for claim in getattr(signals, 'claims', []):
        verdict = claim.get('status', '') or claim.get('verdict', '')
        if 'contradict' in verdict.lower():
            raise_to(BLOCK, f"Claim contradicts: {claim.get('text', '')}")

    for dim, rule in policy["dimension_minimums"].items():
        score = getattr(signals, dim)
        if score < rule["hold_below"]:
            raise_to(HOLD, f"{dim} score {score} is below {rule['hold_below']}")
    if signals.accuracy_status == "not_scored" and policy["when_accuracy_not_scored"] != "ignore":
        raise_to(policy["when_accuracy_not_scored"], "accuracy was not scored (no claims checked)")
    if signals.accuracy_status == "unavailable":
        raise_to(policy["when_accuracy_unavailable"], "fact-check was unavailable")
    tier_decision = policy["tier_decisions"][signals.tier]
    if tier_decision != APPROVE:
        raise_to(tier_decision, f"Ezio tier is {signals.tier} ({signals.score})")
    if not reasons:
        reasons.append(f"No issues found (Ezio {signals.tier} {signals.score})")
    return reasons, decision


def decide(text, context=None, persona="customer_support", scenario=None, audit_log=True):
    context = context or {}
    try:
        response = analyze(text, scenario=scenario)
        signals = parse_signals(response)
    except EzioError as e:
        result = Decision(POLICY["on_ezio_failure"], f"Ezio unavailable: {e}", [],
                          {"error": str(e), "response": e.response})
    else:
        findings = check_source_of_truth(text, context, load_source_of_truth(persona))
        reasons, decision = apply_policy(signals, findings, POLICY)
        result = Decision(
            decision, "; ".join(reasons), [f.message for f in findings],
            response,
        )
    if audit_log:
        audit.record(persona, text, context, result)
    return result


def gate(text, context=None, persona="customer_support"):
    result = decide(text, context, persona)
    print(f"{result.decision}: {result.reason}")
    if result.decision == APPROVE:
        mock_actions.run_for_persona(persona, text)
    else:
        print("[GATE] action not executed")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit('usage: python3 wrapper.py <persona> "<text>" [context-json]')
    gate(sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}, sys.argv[1])
