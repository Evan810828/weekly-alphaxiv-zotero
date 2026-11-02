"""Cloud-only weekly archive. Public output contains counts, never library records."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
ARXIV = re.compile(r"(?<![\w.])(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(?:v\d+)?(?!\d)", re.I)


class Gate(Exception):
    """Only fixed, non-sensitive error codes may cross the logging boundary."""


class HTTPFailure(Gate):
    def __init__(self, service, status):
        self.status = status
        super().__init__(f"{service}_http_{status}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials to a redirect target.
        return None


def request(service, url, method="GET", body=None, headers=None, rpc_id=None):
    data = None if body is None else json.dumps(body).encode()
    h = {"User-Agent": "weekly-alphaxiv-zotero/1.0", **(headers or {})}
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    try:
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=45) as r:
            if rpc_id is not None and "text/event-stream" in r.headers.get("Content-Type", ""):
                lines = []
                for raw_line in r:
                    line = raw_line.decode().rstrip("\r\n")
                    if line.startswith("data:"):
                        lines.append(line[5:].lstrip())
                    elif not line and lines:
                        message = json.loads("\n".join(lines))
                        lines = []
                        if message.get("id") == rpc_id:
                            return json.dumps(message).encode(), dict(r.headers)
                raise Gate("alphaxiv_stream_response_missing")
            return r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        raise HTTPFailure(service, e.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Gate(f"{service}_connection_failed") from None


def monday(day):
    return day - dt.timedelta(days=day.weekday())


def folder_name(day):
    if day.weekday() != 0:
        raise Gate("date_must_be_monday")
    return f"{MONTHS[day.month-1]}-Week{(day.day-1)//7+1}-{day.year}"


def arxiv_id(value):
    m = ARXIV.search(urllib.parse.unquote(str(value or "")))
    return re.sub(r"v\d+$", "", m.group(0), flags=re.I).lower() if m else None


def doi(value):
    v = str(value or "").strip().lower()
    v = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", v)
    return v if re.fullmatch(r"10\.\d{4,9}/\S+", v) else None


def normalized(value):
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value).casefold())


def item_ids(item):
    d = item.get("data", item)
    ids = set()
    if value := doi(d.get("DOI")):
        ids.add("doi:" + value)
    for field in ("DOI", "url", "extra", "archiveID", "archiveLocation"):
        value = d.get(field, "")
        if field == "extra":
            for line in value.splitlines():
                if line.lower().startswith("doi:") and (v := doi(line)):
                    ids.add("doi:" + v)
        if value and (v := arxiv_id(value)):
            ids.add("arxiv:" + v)
    return ids


def match_item(paper, items):
    matches = [i for i in items if item_ids(i) & item_ids(paper)]
    if len(matches) > 1:
        raise Gate("multiple_identifier_matches")
    if matches:
        return matches[0]
    # Missing identifiers must never silently cause a duplicate.
    candidates = [i for i in items if normalized(i["data"].get("title", "")) == normalized(paper["title"])]
    if candidates:
        raise Gate("title_candidate_needs_identifier_review")
    return None


def unpack_tool(result):
    if result.get("isError"):
        raise Gate("alphaxiv_tool_failed")
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content", []):
        if block.get("type") == "text":
            try:
                value = json.loads(block["text"])
                if isinstance(value, dict):
                    return value
            except (ValueError, KeyError):
                pass
    raise Gate("alphaxiv_response_not_structured")


class AlphaXiv:
    def __init__(self, key):
        self.headers = {"Authorization": "Bearer " + key, "Accept": "application/json, text/event-stream"}
        self.counter = 0
        result = self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                          "clientInfo": {"name": "weekly-alphaxiv-zotero", "version": "1.0"}})
        version = result.get("protocolVersion")
        if version not in ("2025-03-26", "2025-06-18", "2025-11-25"):
            raise Gate("alphaxiv_protocol_review_required")
        self.headers["MCP-Protocol-Version"] = version
        self.rpc("notifications/initialized", {}, notification=True)
        names = set()
        cursor = None
        while True:
            page = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            names.update(t["name"] for t in page.get("tools", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        if not {"list_library", "create_folder"} <= names:
            raise Gate("alphaxiv_required_tools_missing")

    def rpc(self, method, params, notification=False):
        self.counter += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.counter
        raw, headers = request("alphaxiv", "https://api.alphaxiv.org/mcp/v1", "POST", payload, self.headers,
                               rpc_id=None if notification else self.counter)
        for k, v in headers.items():
            if k.lower() == "mcp-session-id":
                self.headers["Mcp-Session-Id"] = v
        if notification:
            return {}
        try:
            messages = [json.loads(raw)]
        except ValueError:
            messages = []
            for event in raw.decode().replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(l[5:].lstrip() for l in event.splitlines() if l.startswith("data:"))
                if data:
                    try:
                        messages.append(json.loads(data))
                    except ValueError:
                        pass
        for message in messages:
            if message.get("id") == self.counter:
                if "error" in message:
                    raise Gate("alphaxiv_rpc_failed")
                return message["result"]
        raise Gate("alphaxiv_response_missing")

    def call(self, name, arguments):
        return unpack_tool(self.rpc("tools/call", {"name": name, "arguments": arguments}))

    def library(self):
        data = self.call("list_library", {"include_papers": True})
        if not isinstance(data.get("folders"), list):
            raise Gate("alphaxiv_folders_missing")
        return data["folders"]

    def create(self, name, parent):
        args = {"name": name}
        if parent:
            args["parent_folder_id"] = parent
        # Do not automatically replay a creation after an uncertain response.
        self.call("create_folder", args)


def find_folder(folders, name, parent=None):
    matches = [f for f in folders if f["name"] == name and f.get("parent_id") == parent]
    if len(matches) > 1:
        raise Gate("duplicate_folder_names")
    return matches[0] if matches else None


def ensure_folders(alpha, day, apply, counts):
    folders = alpha.library()
    parent = find_folder(folders, folder_name(day))
    if parent:
        counts["folders_reused"] += 1
    elif apply:
        alpha.create(folder_name(day), None)
        folders = alpha.library()
        parent = find_folder(folders, folder_name(day))
        if not parent:
            raise Gate("parent_folder_verification_failed")
        counts["folders_created"] += 1
    else:
        counts["folders_would_create"] += 2
        return folders
    star = find_folder(folders, "Star", parent["folder_id"])
    if star:
        counts["folders_reused"] += 1
    elif apply:
        alpha.create("Star", parent["folder_id"])
        folders = alpha.library()
        if not find_folder(folders, "Star", parent["folder_id"]):
            raise Gate("star_folder_verification_failed")
        counts["folders_created"] += 1
    else:
        counts["folders_would_create"] += 1
    return folders


def previous_papers(folders, day):
    parent = find_folder(folders, folder_name(day - dt.timedelta(days=7)))
    star = find_folder(folders, "Star", parent["folder_id"]) if parent else None
    if star is None:
        return None
    papers = star.get("papers", [])
    if star.get("papers_truncated") or star.get("paper_count") != len(papers):
        raise Gate("source_folder_incomplete")
    return papers


class Zotero:
    def __init__(self, key, collection):
        self.headers = {"Zotero-API-Key": key, "Zotero-API-Version": "3", "Accept": "application/json"}
        info = self.api("/keys/current")
        access = info.get("access", {}).get("user", {})
        if not access.get("library") or not access.get("write"):
            raise Gate("zotero_personal_library_read_write_required")
        self.prefix = "/users/" + str(info["userID"])
        self.collection = collection
        dest = self.api(self.prefix + "/collections/" + collection)
        if dest["data"]["name"] != "pending_archive":
            raise Gate("zotero_destination_name_mismatch")

    def api(self, path, method="GET", body=None, headers=None):
        raw, response_headers = request("zotero", "https://api.zotero.org" + path, method, body, {**self.headers, **(headers or {})})
        self.last_library_version = next((v for k, v in response_headers.items() if k.lower() == "last-modified-version"), None)
        return json.loads(raw) if raw else None

    def items(self):
        result = []
        start = 0
        snapshot_version = None
        while True:
            page = self.api(self.prefix + f"/items/top?format=json&limit=100&start={start}&includeTrashed=0")
            if self.last_library_version is None:
                raise Gate("zotero_library_version_missing")
            if snapshot_version is not None and self.last_library_version != snapshot_version:
                raise Gate("zotero_changed_during_deduplication")
            snapshot_version = self.last_library_version
            result.extend(i for i in page if i["data"].get("itemType") not in ("attachment", "note"))
            start += len(page)
            if len(page) < 100:
                self.snapshot_version = snapshot_version
                return result

    def get(self, key):
        return self.api(self.prefix + "/items/" + key)

    def file_existing(self, item):
        key = item["key"]
        for _ in range(3):
            current = self.get(key)
            old = set(current["data"].get("collections", []))
            if self.collection in old:
                return "already_present"
            try:
                self.api(self.prefix + "/items/" + key, "PATCH", {"collections": sorted(old | {self.collection})},
                         {"If-Unmodified-Since-Version": str(current["version"])})
            except HTTPFailure as e:
                if e.status == 412:
                    continue
                raise
            after = self.get(key)
            if not (old | {self.collection}) <= set(after["data"].get("collections", [])):
                raise Gate("zotero_membership_verification_failed")
            return "membership_added"
        raise Gate("zotero_concurrent_edit_retry_exhausted")

    def create(self, paper):
        record = {**paper, "collections": [self.collection]}
        # Same payload uses same token, protecting an uncertain replay for 12 hours.
        token = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:32]
        result = self.api(self.prefix + "/items", "POST", [record], {
            "Zotero-Write-Token": token, "If-Unmodified-Since-Version": self.snapshot_version})
        self.snapshot_version = self.last_library_version
        saved = result.get("successful", {}).get("0")
        if not saved or result.get("failed"):
            raise Gate("zotero_create_failed")
        after = self.get(saved["key"])
        if self.collection not in after["data"].get("collections", []) or not (item_ids(paper) & item_ids(after)):
            raise Gate("zotero_import_verification_failed")
        return after


def metadata(source):
    aid = arxiv_id(source.get("universal_paper_id"))
    if source.get("type") != "public" or not aid:
        raise Gate("source_needs_manual_metadata")
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": aid})
    raw, _ = request("arxiv", url)
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    entries = ET.fromstring(raw).findall("a:entry", ns)
    if len(entries) != 1 or arxiv_id(entries[0].findtext("a:id", "", ns)) != aid:
        raise Gate("arxiv_metadata_identity_failed")
    entry = entries[0]
    title = " ".join(entry.findtext("a:title", "", ns).split())
    authors = [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)]
    if not title or not authors:
        raise Gate("arxiv_metadata_incomplete")
    creators = []
    for name in authors:
        parts = name.rsplit(" ", 1)
        creators.append({"creatorType": "author", **({"firstName": parts[0], "lastName": parts[1]} if len(parts) == 2 else {"name": name})})
    return {"itemType": "preprint", "title": title, "creators": creators,
            "date": entry.findtext("a:published", "", ns)[:10], "repository": "arXiv",
            "archiveID": aid, "url": "https://arxiv.org/abs/" + aid,
            "DOI": entry.findtext("x:doi", "", ns) or "10.48550/arXiv." + aid,
            "extra": "arXiv: " + aid}


COUNT_KEYS = ("folders_created", "folders_reused", "folders_would_create", "papers_found", "imported",
              "membership_added", "already_present", "would_import", "would_add_membership", "failed")


def run(alpha, zotero_factory, day, apply=False, metadata_fn=metadata):
    counts = dict.fromkeys(COUNT_KEYS, 0)
    errors = []
    folders = ensure_folders(alpha, day, apply, counts)
    papers = previous_papers(folders, day)
    status = "previous_star_missing" if papers is None else "previous_star_empty" if not papers else "processed"
    # Probe destination even for an empty source.
    try:
        zotero = zotero_factory()
    except Gate as e:
        return {"status": status, "counts": counts, "errors": errors, "error": str(e)}
    if not papers:
        return {"status": status, "counts": counts, "errors": errors}
    counts["papers_found"] = len(papers)
    items = zotero.items()
    for index, source in enumerate(papers):
        try:
            paper = metadata_fn(source)
            item = match_item(paper, items)
            if item:
                if apply:
                    result = zotero.file_existing(item)
                else:
                    result = "already_present" if zotero.collection in item["data"].get("collections", []) else "would_add_membership"
                counts[result] += 1
            elif apply:
                # Rescan immediately before creation, including concurrent additions.
                # The subsequent write uses this snapshot's library version.
                items = zotero.items()
                late_match = match_item(paper, items)
                if late_match:
                    counts[zotero.file_existing(late_match)] += 1
                    continue
                items.append(zotero.create(paper))
                counts["imported"] += 1
            else:
                counts["would_import"] += 1
        except Gate as e:
            counts["failed"] += 1
            errors.append({"paper_number": index + 1, "code": str(e)})
        if metadata_fn is metadata:
            time.sleep(3)  # Respect arXiv API pacing.
    return {"status": status, "counts": counts, "errors": errors}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Perform writes; default is read-only")
    p.add_argument("--monday", help="Explicit YYYY-MM-DD Monday for catch-up or verification")
    args = p.parse_args()
    report = {"mode": "apply" if args.apply else "dry-run"}
    try:
        day = dt.date.fromisoformat(args.monday) if args.monday else monday(dt.datetime.now(TZ).date())
        folder_name(day)
        if day > monday(dt.datetime.now(TZ).date()):
            raise Gate("future_week_not_allowed")
        report["week"] = folder_name(day)
        needed = ("ALPHAXIV_API_KEY", "ZOTERO_API_KEY", "ZOTERO_COLLECTION_KEY")
        if any(not os.environ.get(k) for k in needed):
            raise Gate("required_secrets_missing")
        if not re.fullmatch(r"[A-Z0-9]{8}", os.environ["ZOTERO_COLLECTION_KEY"]):
            raise Gate("invalid_collection_key")
        if args.apply and os.environ.get("OLD_SCHEDULE_CLEARED") != "true":
            raise Gate("old_schedule_clearance_required")
        alpha = AlphaXiv(os.environ["ALPHAXIV_API_KEY"])
        report.update(run(alpha, lambda: Zotero(os.environ["ZOTERO_API_KEY"], os.environ["ZOTERO_COLLECTION_KEY"]), day, args.apply))
    except Gate as e:
        report["error"] = str(e)
    except Exception:
        # Never print raw HTTP bodies, metadata, exception values, URLs, or credentials.
        report["error"] = "unexpected_failure_review_required"
    output = json.dumps(report, indent=2)
    print(output)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write("```json\n" + output + "\n```\n")
    return 1 if report.get("error") or report.get("counts", {}).get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
