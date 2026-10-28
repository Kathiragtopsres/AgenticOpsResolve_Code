#!/usr/bin/env python3
"""Ingest knowledge documents into S3 and (re)sync the Bedrock Knowledge Base (Section 9.3).

Uploads each Markdown document under ``knowledge/`` together with its ``.metadata.json``
sidecar to the ``opsresolve-<env>-knowledge`` S3 bucket, then starts a Knowledge Base
ingestion job. SUPERSEDED documents keep ``docStatus=SUPERSEDED`` in their metadata so the
retrieval filter (docStatus=ACTIVE) excludes them.

Usage:
    python scripts/ingest_knowledge.py --env demo [--dry-run]
"""
from __future__ import annotations

import argparse
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = ROOT / "knowledge"


def discover_documents() -> list[tuple[pathlib.Path, pathlib.Path | None]]:
    """Return (document, metadata-sidecar-or-None) pairs for every KB document."""
    pairs: list[tuple[pathlib.Path, pathlib.Path | None]] = []
    for doc in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        if doc.name.endswith(".metadata.json"):
            continue
        sidecar = doc.with_name(doc.name + ".metadata.json")
        pairs.append((doc, sidecar if sidecar.exists() else None))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default="demo")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bucket = f"opsresolve-{args.env}-knowledge"
    docs = discover_documents()

    s3 = None
    if not args.dry_run:
        import boto3  # noqa: PLC0415

        s3 = boto3.client("s3")

    for doc, sidecar in docs:
        rel = doc.relative_to(KNOWLEDGE_DIR).as_posix()
        if args.dry_run:
            print(f"would upload s3://{bucket}/{rel}" + ("  (+metadata)" if sidecar else ""))
            continue
        s3.upload_file(str(doc), bucket, rel)
        if sidecar:
            s3.upload_file(str(sidecar), bucket, rel + ".metadata.json")
        print(f"uploaded s3://{bucket}/{rel}")

    print(f"{'Would ingest' if args.dry_run else 'Ingested'} {len(docs)} document(s).")
    if not args.dry_run:
        print("TODO: start_ingestion_job on the Knowledge Base data source (bedrock-agent).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
