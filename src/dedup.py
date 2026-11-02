"""Cross-source duplicate identity evidence (source-expansion Phase 4).

This module NEVER changes existing job uids, notified/history/pruned
flags, and never auto-collapses records. It produces two kinds of
evidence only:

* "corroborated" identity: a strong shared key - canonical job URL after
  safe removal of known tracking parameters, same source+source_id, or
  equal description hash with the same employer and a consistent
  country. Safe candidates for a REVIEWED future merge.
* "possible" duplicates: normalised title+company+city+country collide
  but no strong key agrees. Distinct openings can share those fields, so
  these are review flags only and must never merge automatically.

Canonicalisation keeps identity-bearing query parameters; only a fixed
list of known tracking parameters is stripped.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .keywords import fold_text

# Known tracking/analytics parameters only. Generic-looking keys such as
# "id", "ref", "source", "jobId" are identity-bearing on some boards and
# are deliberately NOT stripped.
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_referrer", "utm_social",
    "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    "fbclid", "mc_cid", "mc_eid", "igshid", "msclkid", "twclid",
    "li_fat_id", "_ga", "_gl", "_gac", "yclid", "vero_id",
    "wt_mc", "pk_campaign", "pk_kwd", "hsa_acc", "hsa_cam", "hsa_grp",
    "hsa_ad", "hsa_src", "hsa_tgt", "hsa_kw", "hsa_mt", "hsa_net",
    "hsa_ver",
})


def canonical_url(url: str) -> str:
    """Stable comparison form of a job URL.

    Lower-cases scheme/host, strips a leading "www.", drops the fragment
    and known tracking parameters, and removes a trailing slash on
    non-root paths. Every other query parameter is preserved.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return str(url).strip()
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host
    if port and port not in (80, 443):
        netloc = "{}:{}".format(host, port)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    pairs.sort()
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(pairs), ""))


def _alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold_text(value or ""))


def description_fingerprint(text: str) -> str:
    """Folded, whitespace-collapsed description hash ("" when too short).

    Short descriptions are not hashed: boilerplate snippets collide too
    easily to be identity evidence.
    """
    folded = re.sub(r"\s+", " ", fold_text(text or "")).strip()
    if len(folded) < 200:
        return ""
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()[:16]


def strong_identity_keys(job) -> set:
    """Strong identity keys for one job-shaped record or Job object."""
    get = job.get if isinstance(job, dict) else lambda k, d=None: getattr(job, k, d)
    keys = set()
    url = canonical_url(get("url") or "")
    if url and url != "https://":
        keys.add("url:{}".format(url))
    source = (get("source") or "").strip()
    source_id = (get("source_id") or "").strip()
    if source and source_id:
        keys.add("src:{}|{}".format(source, source_id))
    fp = description_fingerprint(get("description") or "")
    employer = _alnum(get("company") or "")
    country = (get("country") or "").upper()
    if fp and employer and country:
        keys.add("desc:{}|{}|{}".format(fp, employer, country))
    return keys


def weak_key(job) -> str:
    """Normalised title+employer+country: a possible-duplicate signal only."""
    get = job.get if isinstance(job, dict) else lambda k, d=None: getattr(job, k, d)
    return "{}|{}|{}".format(_alnum(get("title") or ""),
                             _alnum(get("company") or ""),
                             (get("country") or "").upper())


def find_duplicate_groups(jobs) -> dict:
    """Group stored jobs by identity evidence. No mutation, no merging.

    Returns {"corroborated": [...], "possible": [...]}; each group lists
    the member uids/urls and the evidence that grouped them.
    """
    jobs = list(jobs)
    by_strong = {}
    strong_keys_per_job = []
    for job in jobs:
        keys = strong_identity_keys(job)
        strong_keys_per_job.append(keys)
        for key in keys:
            by_strong.setdefault(key, []).append(job)

    # Union-find over jobs sharing any strong key.
    parent = list(range(len(jobs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index_of = {id(j): i for i, j in enumerate(jobs)}
    for members in by_strong.values():
        if len(members) > 1:
            root = find(index_of[id(members[0])])
            for other in members[1:]:
                parent[find(index_of[id(other)])] = root
    groups = {}
    for i, job in enumerate(jobs):
        groups.setdefault(find(i), []).append(job)
    corroborated = []
    grouped_ids = set()
    for members in groups.values():
        if len(members) > 1:
            corroborated.append({
                "kind": "corroborated",
                "members": [_member_brief(m) for m in members],
                "evidence": sorted({k for m in members
                                    for k in strong_identity_keys(m)}),
            })
            grouped_ids.update(id(m) for m in members)

    # Weak signal over the remaining jobs.
    by_weak = {}
    for job in jobs:
        if id(job) in grouped_ids:
            continue
        key = weak_key(job)
        if key.strip("|"):
            by_weak.setdefault(key, []).append(job)
    possible = []
    for key, members in by_weak.items():
        if len(members) > 1:
            sources = {_get(m, "source") for m in members}
            if len(sources) > 1:  # same-source collisions share the uid already
                possible.append({
                    "kind": "possible",
                    "members": [_member_brief(m) for m in members],
                    "evidence": ["weak title+employer+country match: {}".format(key)],
                    "note": "distinct openings can share these fields; review only",
                })
    return {"corroborated": corroborated, "possible": possible}


def _get(job, key):
    return job.get(key) if isinstance(job, dict) else getattr(job, key, None)


def _member_brief(job) -> dict:
    return {"uid": _get(job, "uid") or "", "source": _get(job, "source") or "",
            "url": _get(job, "url") or "",
            "title": (_get(job, "title") or "")[:80],
            "company": (_get(job, "company") or "")[:60],
            "country": _get(job, "country") or ""}
