"""Backups command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_backups_coverage(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    """Fleet backup coverage across BOTH tracks (local RMAN/expdp + offsite NAS).

    Defaults to ``at_risk`` because that is what the question almost always means; pass
    ``--verdict all`` for the whole estate. ``not_applicable`` covers cloud RDS (the provider
    backs those up) and cluster components (backup is cluster-level) — counting them as
    at-risk buries the real findings, so they are a separate bucket, not a failure.
    """
    verdict = None if args.verdict == "all" else args.verdict
    return runtime["_request"](
        "GET",
        "/dba/backups/coverage",
        params={
            "verdict": verdict,
            "instance_type": args.instance_type,
            "exclude_cloud": "true" if getattr(args, "exclude_cloud", False) else None,
            "cloud_vendor": getattr(args, "cloud_vendor", None),
            "environment": getattr(args, "environment", None),
            "limit": args.limit,
        },
    )


def cmd_backups(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    # Surfaces backup_method + determination (verified / declared_no_evidence / not_tracked /
    # unknown) so the answer to "does this instance actually have a backup?" is explicit —
    # expdp dumps are invisible to RMAN, so the raw status alone reads as a false "no backup".
    return runtime["_request"](
        "GET",
        f"/instances/{args.instance_id}/backups",
        params={"refresh": args.refresh or None},
    )




def _add_backup_commands(sub, runtime: dict[str, Any]) -> None:
    """备份。"""
    bcov = sub.add_parser(
        "backups-coverage",
        help="Fleet backup coverage, both tracks (v3.32+); defaults to at_risk only",
    )
    bcov.add_argument("--verdict", default="at_risk",
                      choices=["at_risk", "ok", "warning", "indeterminate",
                               "remote_untracked", "not_applicable", "suppressed", "all"])
    bcov.add_argument("--instance-type")
    bcov.add_argument("--exclude-cloud", action="store_true",
                      help="Drop vendor-managed cloud RDS rows (their backups are the provider's).")
    bcov.add_argument("--cloud-vendor")
    bcov.add_argument("--environment")
    bcov.add_argument("--limit", type=runtime["_positive_int"], default=500)
    bcov.set_defaults(func=runtime["cmd_backups_coverage"])

    backups = sub.add_parser(
        "backups",
        help="One instance's backup status + evidence-based determination (has-backup verdict; v2.98+)",
    )
    backups.add_argument("--instance-id", type=int)
    backups.set_defaults(_needs_instance=True)
    backups.add_argument(
        "--instance-ids",
        help="Comma-separated ids: run this for each and return one array. Every id lands in `items` or in `failed` — none is silently dropped.",
    )
    backups.add_argument(
        "--refresh", action="store_true",
        help="Force a live Oracle RMAN refresh (slow); default serves the stored daily-swept status",
    )
    backups.set_defaults(func=runtime["cmd_backups"])
