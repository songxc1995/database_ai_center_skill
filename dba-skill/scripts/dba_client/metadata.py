"""Metadata command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_business_inference_evidence(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    database_id = getattr(args, "database_id", None)
    database_name = getattr(args, "database", None)
    instance_id = getattr(args, "instance_id", None)
    if database_id is None:
        if instance_id is None or not database_name:
            runtime["_fail"](
                "missing_database",
                "business-inference-evidence needs --database-id N, or both --instance-id N and --database NAME",
                exit_code=2,
            )
        matches = runtime["_request"](
            "GET",
            "/dba/databases/search",
            params={
                "q": database_name,
                "instance_id": instance_id,
                "status": "active",
                "include_system_dbs": False,
                "limit": 100,
                "offset": 0,
            },
        )
        candidates = matches.get("items") if isinstance(matches, dict) else None
        exact = [
            row for row in (candidates if isinstance(candidates, list) else [])
            if isinstance(row, dict)
            and str(row.get("database_name") or "").lower() == str(database_name).lower()
            and int(row.get("instance_id") or -1) == int(instance_id)
        ]
        if len(exact) != 1:
            runtime["_fail"](
                "database_not_uniquely_resolved",
                "The instance/database pair did not resolve to exactly one active, non-system database; use --database-id.",
                exit_code=2,
            )
        database_id = int(exact[0]["database_id"])

    # This command intentionally bypasses the live-probe API. It reads the platform snapshot
    # and follows every page so an external model gets the stored catalogue, not an arbitrary
    # alphabetical first page. No request made here can connect to the source database.
    path = f"/dba/metadata/databases/{database_id}/objects"
    payload = runtime["_fetch_all"]("GET", path, {"limit": 500, "offset": 0})
    runtime["_warn_if_truncated"](payload, path)
    rows = payload.get("items") if isinstance(payload, dict) else None
    items = [
        {
            "schema_name": row.get("schema_name"),
            "table_name": row.get("object_name"),
            "object_type": row.get("object_type"),
            "table_comment": row.get("object_comment"),
        }
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
    ]
    tables_with_comments = sum(
        1 for row in items if str(row.get("table_comment") or "").strip()
    )
    status_value = str(payload.get("status") or "never_collected") if isinstance(payload, dict) else "never_collected"
    completeness = payload.get("completeness") if isinstance(payload, dict) else None
    available = status_value not in {"never_collected", "failed", "archived"}
    partial = completeness == "partial" or status_value == "partial" or bool(
        payload.get("truncated") if isinstance(payload, dict) else False
    )
    limitations: list[dict[str, str]] = []
    if not available:
        limitations.append({
            "code": "snapshot_unavailable",
            "message": "No usable persisted metadata snapshot exists; do not infer a business from this response.",
        })
    elif not items:
        limitations.append({
            "code": "empty_metadata_snapshot",
            "message": (
                "The stored snapshot contains no objects. This is insufficient evidence, not proof that "
                "the database has no business purpose; carry the probe note and do not infer."
            ),
        })
    elif partial:
        limitations.append({
            "code": "partial_snapshot",
            "message": (
                "The latest collection was incomplete or this response could not retrieve every stored page; "
                "missing objects may carry different business signals."
            ),
        })
    if available and items and tables_with_comments == 0:
        limitations.append({
            "code": "no_table_comments",
            "message": (
                "No returned table has a comment, so any inference must rely on table names alone "
                "and should be low-confidence unless several names independently agree."
            ),
        })
    return {
        "task": "infer_database_business",
        "database_id": database_id,
        "instance_id": instance_id,
        "database_name": database_name,
        "available": available,
        "evidence_status": "unavailable" if not available else ("ready" if items else "insufficient"),
        "snapshot_status": status_value,
        "completeness": completeness,
        "collected_at": payload.get("collected_at") if isinstance(payload, dict) else None,
        "signal_quality": {
            "tables_returned": len(items),
            "tables_with_comments": tables_with_comments,
            "comment_coverage_pct": round(tables_with_comments * 100 / len(items), 1) if items else 0.0,
            "sample_scope": "unavailable" if not available else ("partial_snapshot" if partial else "complete"),
            "confidence_ceiling": (
                "none" if not items else ("medium" if partial or tables_with_comments == 0 else "high")
            ),
        },
        "total": payload.get("total") if isinstance(payload, dict) else None,
        "truncated": bool(payload.get("truncated")) if isinstance(payload, dict) else False,
        "provenance": "persisted_metadata_directory",
        "limitations": limitations,
        "items": items,
    }


def cmd_metadata_coverage(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    _ = args
    return runtime["_request"]("GET", "/dba/metadata/coverage")


def cmd_database_objects(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        f"/dba/metadata/databases/{args.database_id}/objects",
        params={"limit": args.limit, "offset": args.offset},
    )


def cmd_database_object_changes(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        f"/dba/metadata/databases/{args.database_id}/changes",
        params={"limit": args.limit, "offset": args.offset},
    )


def cmd_search_database_objects(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/dba/metadata/objects/search",
        params={
            "name": args.name,
            "match": args.match,
            "instance_type": args.instance_type,
            "object_type": args.object_type,
            "limit": args.limit,
            "offset": args.offset,
        },
    )


def cmd_refresh_database_metadata(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("POST", f"/dba/metadata/databases/{args.database_id}/refresh")


def cmd_metadata_refresh_status(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("GET", f"/dba/metadata/refresh-runs/{args.run_id}")


def cmd_propose_metadata_refresh(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "POST", "/dba/actions",
        body={
            "action_type": "metadata_refresh",
            "database_id": args.database_id,
            "reason": args.reason,
            "evidence_refs": args.evidence_ref or [],
        },
    )


def cmd_action_order_status(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("GET", f"/dba/actions/{args.order_id}")


def cmd_execute_action_order(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("POST", f"/dba/actions/{args.order_id}/execute")


def cmd_verify_action_order(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"]("POST", f"/dba/actions/{args.order_id}/verify")
