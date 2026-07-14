"""Local graph sync adapter for Memory Growth.

Markdown Wiki remains the source of truth. This adapter derives a stable local
entity/relation event stream that Cognee, Graphiti, or another graph engine can
consume later. The first version writes JSONL and JSON indexes only; no external
service is required.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory_growth import ensure_memory_growth_scaffold, memory_growth_dir


@dataclass(slots=True)
class GraphSyncResult:
    sync_id: str
    node_count: int
    edge_count: int
    event_path: Path
    node_index_path: Path
    edge_index_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "sync_id": self.sync_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "event_path": str(self.event_path),
            "node_index_path": str(self.node_index_path),
            "edge_index_path": str(self.edge_index_path),
        }


def sync_memory_growth_graph() -> GraphSyncResult:
    """Derive graph nodes/edges from Memory Growth wiki pages."""

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    sync_id = f"graph_sync_{_now_stamp()}_{uuid.uuid4().hex[:8]}"
    pages = _load_wiki_pages(root)
    nodes, edges = _derive_graph(pages)

    graph_dir = root / "graph" / "events"
    graph_dir.mkdir(parents=True, exist_ok=True)
    event_path = graph_dir / f"{time.strftime('%Y%m%d')}.graph_sync.jsonl"
    with event_path.open("a", encoding="utf-8") as f:
        for node in nodes:
            f.write(json.dumps(_event(sync_id, "graph_node_upsert", node), ensure_ascii=False, default=str) + "\n")
        for edge in edges:
            f.write(json.dumps(_event(sync_id, "graph_edge_upsert", edge), ensure_ascii=False, default=str) + "\n")

    node_index_path = root / "indexes" / "graph_nodes.json"
    edge_index_path = root / "indexes" / "graph_edges.json"
    node_index_path.parent.mkdir(parents=True, exist_ok=True)
    node_index_path.write_text(json.dumps({"schema_version": 1, "sync_id": sync_id, "nodes": nodes}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    edge_index_path.write_text(json.dumps({"schema_version": 1, "sync_id": sync_id, "edges": edges}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return GraphSyncResult(
        sync_id=sync_id,
        node_count=len(nodes),
        edge_count=len(edges),
        event_path=event_path,
        node_index_path=node_index_path,
        edge_index_path=edge_index_path,
    )


def _load_wiki_pages(root: Path) -> list[dict[str, Any]]:
    specs = [
        ("concept", root / "concepts", "*/*.md"),
        ("playbook", root / "playbooks", "*.md"),
        ("output", root / "outputs", "*/*.md"),
    ]
    pages: list[dict[str, Any]] = []
    for page_type, base, pattern in specs:
        if not base.exists():
            continue
        for path in sorted(base.glob(pattern)):
            if path.name == "README.md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            frontmatter = _frontmatter(text)
            rel = str(path.relative_to(root))
            summary = str(frontmatter.get("summary") or _first_heading(text) or path.stem).strip()
            pages.append(
                {
                    "node_id": _node_id(page_type, rel),
                    "type": page_type,
                    "path": rel,
                    "summary": summary,
                    "category": path.parent.name if page_type != "playbook" else "playbooks",
                    "frontmatter": frontmatter,
                    "source_refs": frontmatter.get("source_refs") if isinstance(frontmatter.get("source_refs"), list) else [],
                    "keywords": _keywords(summary + " " + _body(text)[:1000]),
                }
            )
    return pages


def _derive_graph(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    for page in pages:
        nodes[page["node_id"]] = {
            "id": page["node_id"],
            "kind": page["type"],
            "label": page["summary"][:120] or page["path"],
            "path": page["path"],
            "category": page["category"],
            "keywords": page["keywords"][:12],
            "source": "memory_growth_markdown",
        }
        category_id = _node_id("category", str(page["category"]))
        nodes.setdefault(
            category_id,
            {
                "id": category_id,
                "kind": "category",
                "label": str(page["category"]),
                "source": "memory_growth_markdown",
            },
        )
        _add_edge(edges, page["node_id"], category_id, "BELONGS_TO_CATEGORY", confidence=0.8)

        for ref in page.get("source_refs") or []:
            ref_id = _source_ref_id(ref)
            nodes.setdefault(
                ref_id,
                {
                    "id": ref_id,
                    "kind": "source_ref",
                    "label": str(ref.get("event_id") or ref.get("work_order_id") or ref.get("type") or ref_id),
                    "source": "memory_growth_source_refs",
                    "payload": ref,
                },
            )
            _add_edge(edges, page["node_id"], ref_id, "DERIVED_FROM", confidence=0.9)

    _add_keyword_edges(pages, edges)
    return sorted(nodes.values(), key=lambda x: x["id"]), sorted(edges.values(), key=lambda x: x["id"])


def _add_keyword_edges(pages: list[dict[str, Any]], edges: dict[str, dict[str, Any]]) -> None:
    for idx, left in enumerate(pages):
        left_keywords = set(left.get("keywords") or [])
        if not left_keywords:
            continue
        for right in pages[idx + 1 :]:
            right_keywords = set(right.get("keywords") or [])
            overlap = left_keywords & right_keywords
            if len(overlap) < 2:
                continue
            confidence = min(0.95, 0.35 + len(overlap) * 0.1)
            _add_edge(
                edges,
                left["node_id"],
                right["node_id"],
                "RELATED_BY_KEYWORDS",
                confidence=confidence,
                evidence={"keywords": sorted(overlap)[:8]},
            )


def _add_edge(
    edges: dict[str, dict[str, Any]],
    source: str,
    target: str,
    relation: str,
    *,
    confidence: float,
    evidence: dict[str, Any] | None = None,
) -> None:
    edge_id = _edge_id(source, relation, target)
    edges[edge_id] = {
        "id": edge_id,
        "source": source,
        "target": target,
        "relation": relation,
        "confidence": confidence,
        "evidence": evidence or {},
    }


def _event(sync_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": f"{event_type}_{uuid.uuid4().hex[:12]}",
        "sync_id": sync_id,
        "event_type": event_type,
        "ts_ms": int(time.time() * 1000),
        "payload": payload,
    }


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    out: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        raw = value.strip()
        try:
            out[key.strip()] = json.loads(raw)
        except Exception:
            out[key.strip()] = raw.strip().strip('"')
    return out


def _body(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end != -1 else text


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", text.lower())
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "is", "are", "this", "that", "with", "from"}
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if len(token) <= 1 or token in stop or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= 24:
            break
    return out


def _node_id(kind: str, key: str) -> str:
    return f"{kind}:{_hash(key)}"


def _source_ref_id(ref: dict[str, Any]) -> str:
    key = json.dumps(ref, ensure_ascii=False, sort_keys=True, default=str)
    return f"source_ref:{_hash(key)}"


def _edge_id(source: str, relation: str, target: str) -> str:
    return f"edge:{_hash(source + '|' + relation + '|' + target)}"


def _hash(text: str) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:16]


def _now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")
