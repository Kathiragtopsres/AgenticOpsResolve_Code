SELECT *, topic(3) AS assetId
FROM 'opsresolve/telemetry/+'
WHERE compressorPressureVar > 0.18 OR supplyTempC > 9.0 OR cryoCompressorTempC > 50
