"""Frontmatter YAML e extracao de metadados de notas Markdown."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
INLINE_TAG_RE = re.compile(r"(?<!\w)#([\w/\-]+)")
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            end = index
            break
    if end is None:
        return {}, text
    raw = "\n".join(lines[1:end])
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def dump_frontmatter(meta: dict[str, Any]) -> str:
    if not meta:
        return ""
    dumped = yaml.safe_dump(
        meta,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{dumped}\n---\n"


def normalize_tags(meta: dict[str, Any], body: str = "") -> list[str]:
    tags: list[str] = []
    raw = meta.get("tags") or meta.get("tag") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in re.split(r"[,\s]+", raw) if part.strip()]
    if isinstance(raw, list):
        tags.extend(str(item).lstrip("#") for item in raw)
    tags.extend(INLINE_TAG_RE.findall(body))
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        key = tag.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(tag)
    return result


def extract_links(body: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for match in WIKILINK_RE.findall(body):
        name = match.strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            links.append(name)
    return links


def extract_title(meta: dict[str, Any], body: str, filename: str) -> str:
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    match = H1_RE.search(body)
    if match:
        return match.group(1).strip()
    return Path(filename).stem
