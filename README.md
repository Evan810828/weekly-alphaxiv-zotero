# Weekly alphaXiv to Zotero archive

Runs on a GitHub-hosted Linux computer. Your laptop and Zotero Desktop can be off.
Uses alphaXiv's hosted MCP service and the Zotero Web API. No LLM or paid cloud
service is required by the program. Standard GitHub-hosted runners are free for
public repositories; storage and other services have their own limits.

## Current deployment state

**Manual only. The weekly schedule is intentionally not enabled yet.**
Cloud credentials, an end-to-end run, and retirement of the previous schedule
must be verified before activation. Offline unit tests do not establish access
to either account.

## What it does

1. Calculates the current Monday in `America/Chicago` and creates or reuses
   `MMM-WeekN-YYYY` with its `Star` child. N is the Monday's ordinal in that month.
2. Reads the preceding Monday's Star across month and year boundaries.
3. Matches papers against the entire online Zotero personal library using
   normalized DOI and version-independent arXiv IDs.
4. Reuses matching records and adds the destination collection while preserving
   other memberships, or imports a new bibliographic record with verified arXiv
   metadata. AlphaXiv originals are never removed.
5. Reads each updated or created record back to verify it. Logs contain counts
   and fixed error codes, not paper titles, identifiers, notes, or credentials.

The destination must be named `pending_archive`. The key is supplied privately.
This archives bibliographic records and links; it does not download or upload PDFs.
Zotero Desktop receives online changes on its next successful sync.

## Configure private credentials

In repository **Settings > Secrets and variables > Actions**, add repository secrets:

| Secret | Value |
| --- | --- |
| `ALPHAXIV_API_KEY` | API key for the intended alphaXiv account |
| `ZOTERO_API_KEY` | Zotero Web API key with personal-library read/write access |
| `ZOTERO_COLLECTION_KEY` | Key of the existing `pending_archive` collection |

Use the providers' own account settings to create keys. Do not paste keys into
issues, commits, workflow inputs, or logs. Zotero permissions are library-level;
the code restricts writes to adding records to the configured collection and
adding membership on existing records. No group-library access is needed.

Zotero data sync must already have uploaded the destination and existing records
to the intended online account. Files need not be synced for this metadata workflow.

## Verify and activate

1. Under **Actions > Weekly archive (manual until verified) > Run workflow**, choose
   `dry-run`. This tests both credentials, the online destination, the source
   folder, metadata retrieval and duplicate matching without mutations.
2. Clear or disable the previous scheduled task. Only after that is verified,
   set repository variable `OLD_SCHEDULE_CLEARED` to `true`.
3. Run the workflow with `apply` for an appropriate Monday. Review the summary,
   then repeat it: the second run should report reused folders and already-present
   records, with no duplicate imports.
4. After those checks pass, add the following under `on` in `archive.yml` and set
   repository variable `WEEKLY_ARCHIVE_ENABLED` to `true`:

   ```yaml
   schedule:
     - cron: '0 9 * * 1'
       timezone: America/Chicago
   ```

   The next run is the next future Monday at 09:00 Chicago time. GitHub schedules
   can be delayed or dropped. This is not an exact-time guarantee.

## Recovery and limitations

- Public-repository schedules are automatically disabled after 60 days without
  repository activity. Inspect the Actions schedule periodically and re-enable it
  if needed. This implementation does not manufacture commits to bypass that rule.
- A missed or failed week can be rerun manually with its Monday in `monday`.
  The program does not silently mark a failed archive as completed. It processes
  only the selected week's preceding Star folder, not arbitrary older folders.
- AlphaXiv's folder listing is capped. A truncated list or a count mismatch fails
  with `source_folder_incomplete`; it never reports a partial listing as complete.
- Matching titles without reliable identifiers require review. For
  `title_candidate_needs_identifier_review`, verify the existing record and add
  its authoritative DOI/arXiv URL in Zotero, sync, then rerun. No duplicate is made.
- Multiple matching IDs or duplicate folder names require review rather than a guess.
- Private alphaXiv uploads or non-arXiv sources are reported for manual metadata
  handling. An empty or missing previous Star is reported, but this week's folders
  are still prepared in apply mode.
- Reads and writes use optimistic version checks. Concurrent edits cause a reload
  or a failed run rather than overwriting collection memberships. No automatic
  replay of uncertain folder-creation requests is performed.
- HTTP errors, blocked access, unexpected response formats, expired credentials,
  or protocol changes fail visibly without logging response bodies. A 403 does
  not by itself distinguish a bad key from network filtering.
- The MCP client negotiates the established 2025 handshake protocol and retains
  session IDs. A server that only supports newer protocols requires a client update.
- Public summaries show aggregate counts and error codes. Keep private reports and
  exports out of this repository and its artifacts. Only scheduled/default-branch
  or manual runs receive archive secrets; pull-request tests receive no credentials.

## Development

Python 3.10+ with a system IANA timezone database (available on the Ubuntu runner).
On systems without it, install `tzdata`. The program otherwise uses only the
Python standard library.

```sh
python3 -m unittest discover -s tests -v
python3 archive.py                    # read-only
python3 archive.py --monday YYYY-MM-DD # read-only historical check
python3 archive.py --apply            # requires OLD_SCHEDULE_CLEARED=true
```

Official references: [alphaXiv MCP](https://www.alphaxiv.org/docs/mcp),
[Zotero Web API](https://www.zotero.org/support/dev/web_api/v3/start),
[GitHub scheduling](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Actions secrets](https://docs.github.com/en/actions/concepts/security/secrets).
