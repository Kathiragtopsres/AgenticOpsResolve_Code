"""Business-intent tool adapters (Section 6).

Tools are Lambda functions behind the AgentCore Gateway (MCP), grouped by domain
(Section 6.1). Read tools and write tools are tagged; write tools carry a mandatory
``idempotencyKey`` and are authorised by AgentCore Policy before reaching the adapter.

Domains:
  context     getAssetContext, getServiceHistory, getAssetHealth, getContactPreferences
  knowledge   retrieveTroubleshootingKnowledge
  parts       findAndReservePart, releasePartReservation, transferPart
  workforce   findQualifiedTechnicians, getTechnicianStatus
  scheduling  proposeAppointmentSlots, createServiceAppointment, reassignTechnician, updateWorkOrder
  comms       sendCustomerMessage, requestApproval
  case        checkServiceEntitlement, executeSafeRemoteAction, generateServiceReport, closeServiceCase, getCaseContext, transitionCaseState, scheduleTimer
"""
