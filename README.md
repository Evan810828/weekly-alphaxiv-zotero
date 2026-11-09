# Weekly alphaXiv to Zotero archive

Runs on a GitHub-hosted Linux computer. Your laptop and Zotero Desktop can be off.
Uses alphaXiv's hosted MCP service and the Zotero Web API. No LLM or paid cloud
service is required by the program. Standard GitHub-hosted runners are free for
public repositories; storage and other services have their own limits.

## Current deployment state

**Scheduled for Mondays at 09:00 America/Chicago, including daylight saving time.**
The first scheduled run after setup is November 9, 2026. Hosted account access,
read-only verification, and repeated apply-mode runs were checked on November 2.
The previous schedule has been cleared. The current source record was already
archived; hosted verification reused it without duplicate imports. Creation of
new records and folders is covered by offline tests, not a synthetic live import.

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
| `ALPHAXIV_EXPECTED_FOLDER_ID` | ID of a known existing folder in the correct alphaXiv library; checked before writes |
| `ZOTERO_API_KEY` | Zotero Web API key with personal-library read/write access |
| `ZOTERO_COLLECTION_KEY` | Key of the existing `pending_archive` collection |

Use the providers' own account settings to create keys. Do not paste keys into
issues, commits, workflow inputs, or logs. Zotero permissions are library-level;
the code restricts writes to adding records to the configured collection and
adding membership on existing records. No group-library access is needed.

Zotero data sync must already have uploaded the destination and existing records
to the intended online account. Files need not be synced for this metadata workflow.
Keep the alphaXiv account-check folder: deleting it requires updating the secret
after verifying a replacement folder in the same account.

## Manual runs and schedule controls

**[Open manual execution](https://github.com/Evan810828/weekly-alphaxiv-zotero/actions/workflows/archive.yml)**

1. Sign in to GitHub and click **Run workflow**. Keep the branch set to **main**.
2. Choose **apply** to perform the archive, or **dry-run** to preview without changes.
3. Leave **monday** blank for this week's run. To recover a missed run, enter that
   run's Monday as `YYYY-MM-DD`. For example, after November 9, 2026, enter
   `2026-11-09` to prepare Nov-Week2 and archive Nov-Week1's Star. The date refers
   to the missed execution week, not the source folder's week. Future dates are rejected.
4. Click **Run workflow**, open the new run, and check its **archive summary**.
   Success with zero failures confirms completion. An empty or missing source Star
   is explicitly reported and means there were no source papers to archive.

For several missed weeks, run each Monday separately, oldest first, and wait for
each result. Repeating a completed week reuses folders and identified records;
it does not intentionally create duplicates. If a run failed after partial progress,
resolve the reported error and start a fresh manual run for the same Monday.
Manual and scheduled runs share a queue so they do not edit the library concurrently.

Manual runs use GitHub's hosted computer and the existing encrypted secrets. They
work from a phone or another computer while the laptop is off. They also work when
`WEEKLY_ARCHIVE_ENABLED` is `false`. If GitHub disabled the entire workflow, click
**Enable workflow** first. These steps retry the archive; they are not a general
backup or undo for deleted library data.

The old schedule was already cleared and `OLD_SCHEDULE_CLEARED=true` was set during
setup; no need to repeat that step for manual runs.

The schedule below is installed under `on` in `archive.yml`. Repository
   variable `WEEKLY_ARCHIVE_ENABLED=true` enables it. Set the variable to `false`
   to pause scheduled processing; manual runs remain available.

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
- Matching titles without reliable identifiers require review, with one exception:
  if exactly one same-title item is already in `pending_archive`, it is counted as
  `already_present`. Otherwise `title_candidate_needs_identifier_review` is raised;
  verify the existing record and add its authoritative DOI/arXiv URL in Zotero,
  sync, then rerun. No duplicate is made.
- Multiple matching IDs or duplicate folder names require review rather than a guess.
- A non-arXiv source that carries a DOI is matched against Zotero by that DOI only.
  A match is counted as `already_present` (or filed into `pending_archive` in apply
  mode); its metadata is never fabricated. Private alphaXiv uploads and non-arXiv
  sources with no DOI match are reported for manual metadata handling. An empty or
  missing previous Star is reported, but this week's folders are still prepared in
  apply mode.
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
