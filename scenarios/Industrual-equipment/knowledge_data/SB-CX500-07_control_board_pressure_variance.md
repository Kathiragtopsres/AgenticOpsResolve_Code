# Service Bulletin SB-CX500-07: Compressor control pressure variance

> SYNTHETIC DOCUMENT created by TCS for the OpsResolve hackathon demo. Not a real OEM document.

## Symptom
Compressor-control pressure variance (`compressorPressureVar`) rising above 0.18 over 30-60 minutes, often with supply temperature creeping up by 1-2 C and motor current rising.

## Probable cause
1. Degraded compressor control board (part CTRL-BRD-500) - most common on firmware 4.2.x (confidence high when variance keeps rising after a controller soft reset).
2. Pressure sensor drift (part PRS-SNS-55) - likely if variance oscillates rather than trends.

## Remote actions (allowed)
- `CONTROLLER_SOFT_RESET` - clears transient controller faults. If variance remains above 0.18 after 15 minutes, schedule a field visit.

## Field action
Replace control board CTRL-BRD-500. Estimated repair time 120 minutes. Engineer requires skill HVAC-CHILLER-L2 and a valid CX-500 certification.

## Expected failure window
Unrecoverable compressor trip typically within 6-8 hours of variance exceeding 0.25.
