# CX-500 Troubleshooting Guide (excerpt)

> SYNTHETIC DOCUMENT created by TCS for the OpsResolve hackathon demo. Not a real OEM document.

| Symptom | Checks | Next step |
|---|---|---|
| Supply temperature high | Condenser airflow, refrigerant pressures | See SB-CX500-07 if pressure variance > 0.18 |
| Pressure variance oscillating | Sensor wiring, sensor calibration | `RECALIBRATE_SENSOR`, then PRS-SNS-55 |
| Compressor short-cycling | Setpoints, controller log | `CONTROLLER_SOFT_RESET` |
