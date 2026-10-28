// OpsResolve Cedar policies - ILLUSTRATIVE. Align entity/action names with the schema AgentCore Policy
// generates for opsresolve-gateway before deployment.

// Read tools are always permitted for the agent runtime
permit (principal, action in [AgentCore::Action::"getAssetContext", AgentCore::Action::"getAssetHealth",
        AgentCore::Action::"retrieveTroubleshootingKnowledge", AgentCore::Action::"checkServiceEntitlement",
        AgentCore::Action::"findQualifiedTechnicians", AgentCore::Action::"proposeAppointmentSlots"], resource);

// P-01 Parts: autonomous only at or below threshold and when covered
permit (principal, action == AgentCore::Action::"findAndReservePart", resource)
when { context.input.estimatedUnitValueUsd <= 500 && context.input.covered == true };

// P-02 Remote actions: allow-list per asset, never for regulated imaging without an approval id
permit (principal, action == AgentCore::Action::"executeSafeRemoteAction", resource)
when { ["CONTROLLER_SOFT_RESET","RECALIBRATE_SENSOR","SET_ECO_MODE","ONT_REBOOT","ROUTER_REBOOT","WIFI_CHANNEL_OPTIMISE"].contains(context.input.action) };
permit (principal, action == AgentCore::Action::"executeSafeRemoteAction", resource)
when { context.input.action == "CRYO_COMPRESSOR_RESTART" && context.input has approvalId };

// P-03 Booking and reassignment require customer consent reference and a valid certification
permit (principal, action in [AgentCore::Action::"createServiceAppointment", AgentCore::Action::"reassignTechnician"], resource)
when { context.input has consentRef && context.input.consentRef like "opsr1|*" && context.input.technicianCertValid == true };

// P-04 Service credit auto-apply up to cap
permit (principal, action == AgentCore::Action::"applyServiceCredit", resource)
when { context.input.amountUsd <= context.input.autoApplyCapUsd };

// P-07 Courier self-install: only a customer-replaceable part, and only when the contract covers courier replacement
permit (principal, action == AgentCore::Action::"dispatchCourier", resource)
when { context.input.partCustomerReplaceable == true && context.input.courierCovered == true };

// P-05 Messaging, approvals, reports
permit (principal, action in [AgentCore::Action::"sendCustomerMessage", AgentCore::Action::"requestApproval",
        AgentCore::Action::"generateServiceReport", AgentCore::Action::"updateWorkOrder"], resource);

// P-06 Never close without telemetry verification
permit (principal, action == AgentCore::Action::"closeServiceCase", resource)
when { context.input.verification.telemetryNormal == true };
