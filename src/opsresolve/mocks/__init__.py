"""Mock enterprise systems (Section 6.4).

Simulators backed by API Gateway + Lambda + DynamoDB, seeded from ``seed/``. Adapters in
``tools/`` call these over the same tool contract a production adapter would use, so the
agent layer is unchanged when swapping in a real system.

  crm      CRM / Contracts (customers, sites, contacts, SLA tiers)
  eam      EAM / Asset registry (assets, work-order history)
  erp      ERP / Inventory (parts, depots, stock, transfer lead times)
  fsm      WFM / FSM (technicians, skills, certifications, shifts, work orders, live position)
  device   Device management (allow-listed remote actions with scripted outcomes)
"""
