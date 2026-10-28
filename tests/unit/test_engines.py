from datetime import datetime, timezone
from opsresolve_ref.engines import Technician, rank, score, risk, option_score, propose_slots

def techs():
    return [
        Technician("TECH-204", "R. Kumar", {"HVAC-CHILLER-L2": 4}, {"CX-500": "2027-03-31"}, "SOUTH", 60, {"CX-500": 0.86}, 0.55),
        Technician("TECH-311", "S. Menon", {"HVAC-CHILLER-L2": 5}, {"CX-500": "2026-12-31"}, "SOUTH", 150, {"CX-500": 0.91}, 0.40),
        Technician("TECH-118", "D. Pillai", {"HVAC-CHILLER-L1": 3}, {"CX-500": "2027-01-31"}, "SOUTH", 20, {}, 0.10),
        Technician("TECH-402", "M. Iqbal", {"HVAC-CHILLER-L2": 4}, {"CX-500": "2026-09-30"}, "SOUTH", 30, {"CX-500": 0.80}, 0.30),
        Technician("TECH-507", "J. Thomas", {"HVAC-CHILLER-L2": 4}, {"CX-500": "2027-06-30"}, "WEST", 25, {"CX-500": 0.88}, 0.20),
    ]

def test_hard_filters():  # UT-EN-01
    ranked, rejected = rank(techs(), "HVAC-CHILLER-L2", "CX-500", "SOUTH", "2026-10-20")
    assert set(ranked) == {"TECH-204", "TECH-311"}
    assert rejected == {"TECH-118": "SKILL_MISSING", "TECH-402": "CERT_EXPIRED", "TECH-507": "OUT_OF_REGION"}

def test_score_formula():  # UT-EN-02
    t = techs()[0]
    # 0.40*(1-60/240) + 0.25*(4/5) + 0.20*0.86 + 0.15*(1-0.55)
    assert score(t, "HVAC-CHILLER-L2", "CX-500") == round(0.30 + 0.20 + 0.172 + 0.0675, 4)

def test_ranking_order():  # UT-EN-03
    ranked, _ = rank(techs(), "HVAC-CHILLER-L2", "CX-500", "SOUTH", "2026-10-20")
    assert ranked == ["TECH-204", "TECH-311"]

def test_neighbouring_region_allowed():  # UT-EN-04 (S3 recovery)
    ranked, _ = rank(techs(), "HVAC-CHILLER-L2", "CX-500", "SOUTH", "2026-10-20", regions_allowed=["SOUTH", "WEST"])
    assert "TECH-507" in ranked

U = timezone.utc
def test_risk_high_s1_delay():  # UT-EN-05 (S1 recovery trigger)
    r = risk(now=datetime(2026, 10, 20, 12, 30, tzinfo=U), remaining_job_min=100, travel_min=60,
             slot_start=datetime(2026, 10, 20, 14, 0, tzinfo=U), restore_by=datetime(2026, 10, 20, 16, 30, tzinfo=U),
             est_repair_min=120)
    assert r["risk"] == "HIGH" and r["slackMin"] == -55 and r["slaSlackMin"] == -40

def test_risk_low():  # UT-EN-06
    r = risk(datetime(2026, 10, 20, 12, 30, tzinfo=U), 20, 40, datetime(2026, 10, 20, 14, 0, tzinfo=U),
             datetime(2026, 10, 20, 16, 30, tzinfo=U), 90)
    assert r["risk"] == "LOW"

def test_risk_medium():  # UT-EN-07
    r = risk(datetime(2026, 10, 20, 12, 30, tzinfo=U), 60, 50, datetime(2026, 10, 20, 14, 0, tzinfo=U),
             datetime(2026, 10, 20, 17, 30, tzinfo=U), 90)
    assert r["risk"] == "MEDIUM"

def test_option_score():  # UT-EN-08
    alt = option_score(True, 0.2, 0.3, 0.9)
    keep = option_score(False, 0.0, 0.0, 0.86)
    assert alt > keep

def test_slots_sla_first_and_blocked_hours():  # UT-EN-09
    d = lambda h: datetime(2026, 10, 20, h, 0, tzinfo=U)
    c = [{"slotId": "A", "start": d(9), "end": d(11), "technicianId": "T1", "score": 0.9},
         {"slotId": "B", "start": d(14), "end": d(16), "technicianId": "T1", "score": 0.8},
         {"slotId": "C", "start": d(20), "end": d(22), "technicianId": "T2", "score": 0.7},
         {"slotId": "D", "start": d(15), "end": d(17), "technicianId": "T2", "score": 0.95, "partReady": False}]
    out = propose_slots(c, restore_by=d(16), blocked_hours=(8, 12))
    assert [s["slotId"] for s in out] == ["B", "C"]
    assert out[0]["meetsSla"] and not out[1]["meetsSla"]
