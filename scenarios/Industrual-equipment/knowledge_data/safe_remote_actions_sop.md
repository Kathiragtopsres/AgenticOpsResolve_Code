# Safe Remote Actions SOP

> SYNTHETIC DOCUMENT created by TCS for the OpsResolve hackathon demo. Not a real OEM document.

Remote actions may be executed without on-site personnel only if: the asset is not in ALARM_CRITICAL, the action is on the model allow-list, and no one is working on the asset (lock-out/tag-out flag false).
Allow-list for CX-500: CONTROLLER_SOFT_RESET, RECALIBRATE_SENSOR, SET_ECO_MODE. Never remotely bypass safety interlocks.
