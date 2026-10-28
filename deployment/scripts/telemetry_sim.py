#!/usr/bin/env python3
"""Replay a telemetry profile CSV to AWS IoT Core (or stdout with --dry-run).

Usage: python telemetry_sim.py profiles/s1_chiller_degrading.csv --speed 60 [--dry-run]
--speed = demo clock factor (60 => 5 simulated minutes every 5 seconds).
"""
import argparse, csv, json, time
p = argparse.ArgumentParser()
p.add_argument("profile"); p.add_argument("--speed", type=float, default=60.0)
p.add_argument("--dry-run", action="store_true"); p.add_argument("--endpoint", default=None)
a = p.parse_args()
client = None
if not a.dry_run:
    import boto3
    client = boto3.client("iot-data", endpoint_url=a.endpoint) if a.endpoint else boto3.client("iot-data")
rows = list(csv.DictReader(open(a.profile)))
for i, r in enumerate(rows):
    payload = {k: (float(v) if v.replace(".", "", 1).replace("-", "", 1).isdigit() else (v or None)) for k, v in r.items()}
    topic = f"opsresolve/telemetry/{r['assetId']}"
    if a.dry_run:
        print(topic, json.dumps(payload))
    else:
        client.publish(topic=topic, qos=1, payload=json.dumps(payload))
    if i < len(rows) - 1:
        time.sleep(300 / a.speed)
