"""Inbound processing (Section 7.7).

  normalizer     SQS FIFO consumer -> normalised events on opsresolve-bus
  postback       Signed postback sign/verify (Section 7.2.3)
  flow_endpoint  WhatsApp Dynamic Flow endpoint (live slots, no LLM)
"""
