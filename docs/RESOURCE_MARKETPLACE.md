# Resource Marketplace

The Resource Marketplace is the native way to download, verify, install, activate,
update and remove **translation packages**, **AI prompt templates**, **AI workflow
templates**, **agent capabilities**, **knowledge resources**, **model profiles** and
future AI extensions from inside Frappe v17.

It behaves like an enterprise app store:

1. Open `/app/resource-marketplace`.
2. Browse resource cards with live compatibility, checksum/signature status and
   dependencies.
3. Click **Download**.
4. The download happens on a background worker; the UI polls real-time
   progress, speed, ETA, connection quality, verification and installation stages.
5. On completion the download **automatically leaves the Downloads panel** and the
   installed resource appears under **Installed**, already activated and usable in
   the matching AI/translation interface.
6. Updates, rollbacks, removal and monitoring are managed from the same page.

## Architecture

```
User Interface Layer        resource_marketplace page
        |
API / RPC Layer            ai_fr_hg/api/resources.py
        |
Resource Services          ai_fr_hg/ai/resources/
   - catalog.py             discovery, seeding, compatibility
   - download.py            durable download engine + background jobs
   - install.py             installation engine
   - installers.py          resource-type installers
   - lifecyle.py            update, rollback, remove, usage
   - verification.py        SHA-256 + HMAC signature checking
   - recovery.py            stale-worker recovery
   - monitoring.py          usage metrics and recommendations
        |
Registry / Storage         AI Resource* DocTypes
```

The UI never writes configuration directly, never copies files and never creates
database records itself. Every operation goes through the whitelisted API, which
performs role checks and then calls a service. The service persists durable state
in `AI Resource Download`, `AI Resource Install`, `AI Resource Version` and
`AI Resource Event`.

## Lifecycle

```
Discover
  → Evaluate Compatibility
  → Preparing Download
  → Downloading            (resumable, checkpoints preserved)
  → Verifying Integrity    (SHA-256 and digital signature)
  → Installing             (extract, dependencies, register, indexes/config)
  → Registering
  → Activating
  → Ready for Use
  → Monitor → Update → Rollback → Remove
```

A completed download is never shown as pending. The Downloads panel filters to
non-terminal statuses; `AI Resource Install` is the durable "Installed" record.

## Resource Types

| Type | Installs into |
| --- | --- |
| Translation Package | `AI Translation Glossary` |
| Translation Memory Pack | `AI Translation Glossary` |
| AI Prompt Template | `AI Prompt Template` |
| AI Workflow Template | `AI Pipeline` |
| Agent Capability | `AI Skill` |
| Knowledge Resource | `AI Knowledge Base` |
| AI Model | `AI Model` (profile registration) |
| Language Pack | registry metadata |
| AI Extension | registry metadata |

Installers are idempotent, so re-downloading an installed resource updates the
target records without duplicating them.

## Background jobs and recovery

Downloads are enqueued into Frappe's `long` queue with a deterministic `job_id`
so a refresh never queues a duplicate. Progress and checkpoints are written to
`AI Resource Download`. The `*/15 * * * *` scheduler task
`ai_fr_hg.tasks.recover_resource_downloads` marks downloads that stopped
heartbeating as `Retrying`; the UI offers **Retry**, or **Resume** from the
last checkpoint.

## Permissions

- `AI User` may browse the catalog, view downloads and see installed resources.
- `AI Manager` and `System Manager` may download, install, update, rollback,
  remove and sync the catalog.
- `AI Auditor` gets read-only visibility into all resource events and history.
- Every manager operation writes an `AI Audit Log` row and a `AI Resource Event`.

## Bundled catalog

The app ships a signed **Built-in Marketplace** in
`ai_fr_hg/ai/resources/bundles/`. After installation, `refresh_builtin_catalog`
creates/updates the repository and resources and computes each bundle's SHA-256
and a site-keyed HMAC signature. Downloads verify both before installing.

To allow a private enterprise repository, add an `AI Resource` row with a
`https://` source URL and register the hostname in **AI Platform Settings →
Resource Allowed Hosts**. The download engine refuses public hosts in
strict-local mode, disables redirects and uses `Range` requests for resume.

## Offline deployment

Installed bundles are snapshotted under the site's private files. Rollback and
offline reinstall read the snapshot rather than the network. A signed bundle
JSON is self-contained, so an operator can copy it to an air-gapped site and
register it as an `AI Resource`.

## Verification status fields

| Field | Meaning |
| --- | --- |
| Available | Ready to download, environment support checked. |
| Incompatible | Platform/provider/version constraints are not met. |
| Installed | Currently active and usable. |
| Update Available | A newer catalog version exists. |
| Downloading | A background worker is in progress. |
| Deprecated / Security Restricted | Not recommended or blocked by policy. |
