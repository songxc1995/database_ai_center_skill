"""Knowledge command implementations; invoked through the CLI adapter."""

from __future__ import annotations

import argparse
from typing import Any


def cmd_kb_search(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/knowledge/entries",
        params={
            "q": args.q,
            "keyword": args.keyword,
            "db_type": args.db_type,
            "rule_id": args.rule_id,
            "sort": args.sort,
            "limit": args.limit,
            "offset": args.offset,
        },
    )


def cmd_kb_incidents(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    key = runtime["urllib"].parse.quote(args.root_cause_key, safe="")
    return runtime["_request"](
        "GET",
        f"/knowledge/entries/{key}/incidents",
        params={"db_type": args.db_type, "rule_id": args.rule_id, "limit": args.limit},
    )


def cmd_kb_doc_search(args: argparse.Namespace, runtime: dict[str, Any]) -> Any:
    return runtime["_request"](
        "GET",
        "/knowledge/documents/search",
        params={"q": args.q, "limit": args.limit},
    )




def _add_knowledge_commands(sub, runtime: dict[str, Any]) -> None:
    """知识库。"""
    kb_search = sub.add_parser(
        "kb-search", help="Knowledge base: DBA-confirmed symptom->root-cause->remediation history"
    )
    kb_search.add_argument("--q", help="semantic query (embeds the text; widened recall)")
    kb_search.add_argument("--keyword", help="literal keyword match, e.g. ORA-00060")
    kb_search.add_argument("--db-type", choices=runtime["_KB_DB_TYPES"])
    kb_search.add_argument("--rule-id")
    kb_search.add_argument("--sort", choices=["frequency", "recency"])
    kb_search.add_argument("--limit", type=int)
    kb_search.add_argument("--offset", type=int)
    kb_search.set_defaults(func=runtime["cmd_kb_search"])

    kb_incidents = sub.add_parser(
        "kb-incidents", help="Knowledge base: drill down to the raw incidents behind one root cause"
    )
    kb_incidents.add_argument("--root-cause-key", required=True)
    kb_incidents.add_argument("--db-type", choices=runtime["_KB_DB_TYPES"])
    kb_incidents.add_argument("--rule-id")
    kb_incidents.add_argument("--limit", type=int)
    kb_incidents.set_defaults(func=runtime["cmd_kb_incidents"])

    kb_doc_search = sub.add_parser(
        "kb-doc-search", help="Knowledge base: semantic search over curated ops-runbook documents"
    )
    kb_doc_search.add_argument("--q", required=True)
    kb_doc_search.add_argument("--limit", type=int)
    kb_doc_search.set_defaults(func=runtime["cmd_kb_doc_search"])
