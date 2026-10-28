import pytest
from opsresolve_ref.router import select_channel
from opsresolve_ref.state_machine import transition, replay, IllegalTransition
ALL = {"RCS": "OPTED_IN", "SMS": "OPTED_IN", "WHATSAPP": "OPTED_IN", "EMAIL": "OPTED_IN"}

def test_preferred():  # UT-RT-01
    assert select_channel("APPOINTMENT_OPTIONS", ALL, "RCS") == ["RCS"]

def test_p1_dual_send():  # UT-RT-02
    assert select_channel("URGENT_ALERT", ALL, "RCS", priority="P1") == ["RCS", "SMS"]

def test_stay_in_whatsapp():  # UT-RT-03
    assert select_channel("RECOVERY_OFFER", ALL, "RCS", active_channel="WHATSAPP", wa_window_open=True) == ["WHATSAPP"]

def test_whatsapp_window_closed_falls_back():  # UT-RT-04
    assert select_channel("RECOVERY_OFFER", ALL, "WHATSAPP", active_channel="WHATSAPP", wa_window_open=False) == ["WHATSAPP"]

def test_opt_out_respected():  # UT-RT-05 / TC-13
    c = dict(ALL, RCS="OPTED_OUT")
    assert select_channel("APPOINTMENT_OPTIONS", c, "RCS") == ["WHATSAPP"]

def test_report_email():  # UT-RT-06
    assert select_channel("REPORT", ALL, "RCS") == ["EMAIL"]

def test_approval_sms_only():  # UT-RT-07
    assert select_channel("APPROVAL_REQUEST", ALL, "RCS") == ["SMS"]

def test_unhealthy_channel_skipped():  # UT-RT-08
    assert select_channel("ETA_UPDATE", ALL, "RCS", unhealthy={"RCS"}) == ["WHATSAPP"]

def test_no_consent():  # UT-RT-09
    assert select_channel("CSAT", {"RCS": "OPTED_OUT"}, "RCS") == []

def test_s1_golden_path_legal():  # UT-SM-01
    assert replay(["OPEN", "CONTEXT_READY", "DIAGNOSING", "REMOTE_FIX_IN_PROGRESS", "PLANNING_FIELD",
                   "SCHEDULED", "AT_RISK", "SCHEDULED", "IN_SERVICE", "VERIFYING", "CLOSED"])

def test_illegal_skip():  # UT-SM-02
    with pytest.raises(IllegalTransition):
        transition("DIAGNOSING", "SCHEDULED")

def test_escalate_from_any():  # UT-SM-03
    assert transition("SCHEDULED", "ESCALATED_HUMAN") == "ESCALATED_HUMAN"

def test_closed_is_terminal():  # UT-SM-04
    with pytest.raises(IllegalTransition):
        transition("CLOSED", "PLANNING_FIELD")
