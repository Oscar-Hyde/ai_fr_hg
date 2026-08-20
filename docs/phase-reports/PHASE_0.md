# Phase 0 — Truthful Baseline and Quality Gate

**Objective:** establish a truthful product baseline and an executable, Frappe-v17-specific quality gate before changing application behavior.

**Opened:** 2026-08-19
**Phase owner:** Release Engineering
**Status:** OPEN — four hosted checks green on `61d1926`; `main` still unprotected

This is the controlled inventory, contract, and eventual completion report for Phase 0. Phase 1 must not begin until the verdict at the end of this document is `PASS`.

## Inspection evidence

The inspect step covered:

- both workflows under `.github/workflows/`;
- `pyproject.toml`, `.pre-commit-config.yaml`, hooks, scheduler events, patches, install behavior, and repository test layout;
- all 47 DocType schemas, 4 Desk pages, 5 workspaces, 3 reports, and public API/service paths identified by the audit;
- README and every document under `docs/`;
- unsupported controls and claims in DocType metadata, Python type declarations, reader registration, and UI labels;
- recent GitHub Actions runs, repository rulesets, and `main` branch status;
- upstream Frappe framework branches, v17 development version, Python/Node requirements, and upstream server-test environment.

Observed external state on 2026-08-19:

- latest `CI / Server` run `32272212940` failed with **zero steps executed**;
- `main` reports `protected: false` and no required status checks;
- rulesets are unavailable for this private repository on its current GitHub plan;
- the Arena GitHub integration cannot administer classic branch protection;
- upstream Frappe has no stable v17 tag or `version-17` branch; `develop` identifies as `17.0.0-dev`.

## Phase inventory

| ID | Finding | Current State | Required State | Files | Tests | Migration | Frontend | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0-01 / OPS-01 | CI execution | Hosted Server + Quality execute on pinned Frappe v17. | Pin the audited Frappe v17 revision; run Server, Linter, frontend static, and dependency checks; obtain green hosted runs. | `.github/workflows/*.yml` | Workflow contract checks plus hosted runs | None | Static JavaScript parse gate | COMPLETE — PR #31 SHA `61d1926` all four statuses green |
| P0-02 / OPS-01 | Branch protection | `main` is unprotected. | Require all Phase 0 checks and pull requests before merge. | GitHub repository settings; documented policy | GitHub API verification | None | N/A | BLOCKED — GitHub App lacks administration permission |
| P0-03 / SEC-05 | Encryption claim | A visible setting implies stored-text encryption although no encryption exists. | Preserve schema compatibility but disable and hide the setting; state plainly that application-level document encryption is unsupported. | Platform Settings schema/controller, docs | Metadata and controller regression | Idempotently normalize dormant setting to `0` | Setting absent from Desk | COMPLETE — local evidence |
| P0-04 / ING-01 | Folder ingestion claim | `Folder` is selectable but ingestion rejects it. | Remove it from selectable source types and reject new Folder-source records clearly; retain native Frappe File folders as the only folder authority. | AI Document schema/controller, docs | Metadata and validation regression | Preserve legacy rows without destructive conversion | Option absent from form | COMPLETE — local evidence |
| P0-05 / RET-05 | Reranker claim | `Reranker` is selectable/discovered but has no execution path. | Remove it from selectable/discoverable model types; preserve and disable legacy rows. | AI Model schema/controller, monitoring, docs | Metadata, model validation, and inference regression | Idempotently disable legacy rows | Option absent from form | COMPLETE — local evidence |
| P0-06 / ING-03 | MSG claim | `.msg` is registered against an RFC-email reader and is not Outlook MSG parsing. | Remove `.msg` registration and document `.eml` as the supported email format. | Reader registry, docs | Registry regression | None | Unsupported format absent from format API | COMPLETE — local evidence |
| P0-07 / ING-02 | Scanned-PDF OCR claim | Image OCR exists; scanned-PDF OCR does not, but guidance tells users to enable OCR. | Keep PDF text-layer extraction and image OCR; explicitly reject the scanned-PDF OCR promise. | PDF reader warnings, README/docs | Product-contract regression | None | Accurate warning/failure state | COMPLETE — local evidence |
| P0-08 / INT-01 | Target DocType extraction claim | `target_doctype` is visible but extraction never maps or writes records. | Hide/read-only the dormant field while preserving data; extraction remains JSON-only until Phase 4. | Extraction Schema metadata, docs | Metadata regression | None | Field absent from normal forms | COMPLETE — local evidence |
| P0-09 / PROV-03 | Model-version claim | Versions child table is visible but no lifecycle populates it. | Hide/read-only the dormant section/table while preserving rows; defer lifecycle to Phase 6. | AI Model metadata, docs | Metadata regression | None | Section absent from normal forms | COMPLETE — exposure only; PROV-03 remains open |
| P0-10 / TRN-06 | Original-format translation claim | `Preserve Formatting` can be read as binary-format reconstruction. | Label behavior as preservation of extracted-text structure; explicitly state no DOCX/PDF/etc. reconstruction. | Translation metadata, README/docs | Metadata and product-contract regression | None | Accurate label/help | COMPLETE — local evidence |
| P0-11 | Architecture decisions | Database and remove/implement choices are implicit. | Record accepted decisions, Frappe-native alternatives evaluated, rationale, consequences, and revisit phase. | `docs/ARCHITECTURE_DECISIONS.md` | Documentation contract checks | None | N/A | COMPLETE — local evidence |
| P0-12 | Controlled gap register | Findings exist only in the audit narrative. | Register all 79 audit IDs with phase, owner, status, disposition, and acceptance evidence. | `docs/GAP_REGISTER.md` | Completeness regression against audit IDs | None | N/A | COMPLETE — local evidence |
| P0-13 | Product baseline | Several absolute or stale claims remain in README/configuration/translation/extending/status docs. | Describe only tested current behavior and identify security, scale, and runtime limitations. | README and docs | Claim regression searches | None | Accurate Desk-facing descriptions | COMPLETE — local evidence |

## Contracts

### P0-01 — Quality-gate contract

- **Inputs:** a push, pull request, or manual dispatch for a repository revision.
- **Outputs:** independent, required statuses named `Server`, `Linter`, `Frontend static`, and `Dependency audit`.
- **Framework/runtime:** Frappe `17.0.0-dev` at the immutable revision recorded in the architecture decisions; Python 3.14; Node 24; MariaDB 11.8; Redis services.
- **Permissions:** workflows receive read-only repository contents unless an action requires less; no application or provider secrets.
- **Failure states:** setup, install, migration, build, tests, lint, static parsing, Semgrep, or dependency audit failures fail their status. A job with zero steps is not evidence of success.
- **Concurrency:** superseded runs on the same workflow/ref cancel; runs on different refs do not share a cancellation key.
- **Idempotency:** each job creates an isolated runner/bench/site and can be rerun safely.
- **Background behavior/cancellation:** GitHub cancellation terminates the isolated runner. Application workers/providers are not required for the stubbed PR suite.
- **Persistence/audit:** GitHub retains run logs; the workflow verifies the checked-out Frappe revision.
- **Security:** least-privilege workflow token; pinned action major versions; no untrusted provider network access.
- **Frontend:** production JavaScript must at least parse. Browser/E2E is deliberately deferred to Phase 7 and is not represented as present.
- **Performance:** Server receives a 45-minute timeout; lint/security jobs receive bounded timeouts.

### P0-02 — Branch-protection contract

- `main` accepts changes only through pull requests.
- `Server`, `Linter`, `Frontend static`, and `Dependency audit` must be required and current before merge.
- Stale approvals are dismissed when code changes, force pushes/deletions are blocked, conversations must be resolved, and administrators do not bypass the rule.
- Repository-owner configuration is required because application code cannot enforce GitHub branch policy.

### P0-03 through P0-10 — Truthful-capability contract

- **Inputs:** metadata shown in Desk, supported-format discovery, model discovery, document/model validation, and documentation read by operators.
- **Outputs:** only implemented capabilities are selectable or described as functional.
- **Permissions:** hiding a field is not authorization. Existing service/DocType permissions remain authoritative; unsupported values are rejected server-side where callers can create them directly.
- **Failure states:** unsupported Folder sources, reranker model types, and `.msg` files fail with a clear unsupported-capability result rather than entering a false success path.
- **Concurrency/idempotency/jobs/cancellation:** no new job path is introduced. Existing unsupported records are preserved; the normalization patch is idempotent.
- **Data persistence:** no document content, extracted output, model identity, or historical child row is deleted. Dormant security settings are reset because they never provided encryption. Legacy reranker rows are retained but disabled.
- **Audit/security:** no setting may imply a security boundary that does not exist. Network and permission caveats remain visible until their owning findings close.
- **Frontend:** controls are removed or relabeled in DocType metadata, not merely hidden with ad-hoc JavaScript.
- **Migration:** one post-model-sync patch normalizes only unsupported control state and is safe on fresh or upgraded sites.
- **Performance:** metadata checks and normalization are bounded; no corpus scan is introduced.

### P0-11 — Architecture-decision contract

Each decision records status, context, evaluated Frappe v17 capability, decision, rationale, consequences, and revisit trigger. Accepted Phase 0 decisions are:

1. MariaDB-only application support;
2. no application-level stored-document encryption;
3. no synthetic `AI Document` Folder source;
4. no reranker until a real retrieval execution contract exists;
5. no exposed model-version lifecycle until provider lifecycle integration exists;
6. extracted-text-structure translation only, not original binary reconstruction;
7. pin the current Frappe v17 development revision until an upstream stable v17 ref exists.

### P0-12 — Gap-register contract

- The source set is every heading matching an audit ID in `DEVELOPMENT_PLAN.md`.
- Every ID appears exactly once in `GAP_REGISTER.md`.
- Every row has an owning phase, accountable engineering role, status, disposition, and acceptance evidence.
- `CLOSED` requires evidence; decisions to remove/hide are valid only when code, UI metadata, docs, compatibility/data handling, and regression tests agree.

## Frappe v17-native integration review

Phase 0 intentionally uses native framework mechanisms:

- DocType JSON metadata (`hidden`, `read_only`, Select options, descriptions) for Desk exposure;
- DocType controller validation for server-authoritative unsupported-value rejection;
- an idempotent Frappe patch for upgrade normalization;
- Frappe `File` as the sole folder tree authority instead of inventing recursive folder-source semantics;
- Bench site creation, app installation, migration, asset build, and `run-tests` in CI.

No custom feature flag, schema registry, folder tree, migration runner, or test harness is introduced.

## Evidence and completion report

### Findings completed

- **SEC-05 — CLOSED BY REMOVAL.** Application-level stored-document encryption is explicitly unsupported. The compatibility field is hidden/read-only/default-off, direct enablement is rejected, and migration resets stale values.
- **RET-05 — CLOSED BY REMOVAL.** Reranker is absent from selectable and auto-discovered model types. Known reranker names are returned as unsupported; legacy rows are retained but disabled.
- **ING-01 — CLOSED BY REMOVAL.** Folder is absent from AI Document source options and is rejected server-side. Native Frappe File remains the folder authority.
- **ING-02 — CLOSED BY SCOPING.** Image OCR remains optional; scanned-PDF OCR is explicitly unsupported in metadata, reader warnings, and docs.
- **ING-03 — CLOSED BY REMOVAL.** Outlook `.msg` is no longer mapped to the RFC/MIME email reader or returned by the supported-format API. `.eml` remains supported.
- **INT-01 — CLOSED BY REMOVAL.** Target DocType extraction is hidden/read-only and described as compatibility-only; extraction is truthfully JSON-only.
- **TRN-06 — CLOSED BY SCOPING.** The translation control and API/docs promise extracted-text blocks/separators only, never source-binary reconstruction.
- **OPS-01 — PARTIAL.** Hosted Server/Linter/Frontend static/Dependency audit passed on `61d1926`. Branch protection on `main` is still off.

The model-version exposure portion of PROV-03 is hidden, but PROV-03 remains open for its Phase 6 provider/model lifecycle contract.

### Files changed

The list below is exact for this Phase 0 working tree. The broad Python/JavaScript set includes deterministic Ruff/Prettier baseline normalization required to make the configured linter gate pass; functional Phase 0 changes are concentrated in workflows, metadata/controllers/readers/model discovery, tests, patch registration, and documentation.

<details>
<summary>116 files</summary>

- `.github/workflows/ci.yml`
- `.github/workflows/linter.yml`
- `README.md`
- `ai_fr_hg/ai/automation.py`
- `ai_fr_hg/ai/document_tree.py`
- `ai_fr_hg/ai/engine.py`
- `ai_fr_hg/ai/folders.py`
- `ai_fr_hg/ai/ingestion.py`
- `ai_fr_hg/ai/knowledge.py`
- `ai_fr_hg/ai/language.py`
- `ai_fr_hg/ai/monitoring.py`
- `ai_fr_hg/ai/pipeline.py`
- `ai_fr_hg/ai/readers/__init__.py`
- `ai_fr_hg/ai/readers/office.py`
- `ai_fr_hg/ai/settings.py`
- `ai_fr_hg/ai/tools/__init__.py`
- `ai_fr_hg/ai/translation.py`
- `ai_fr_hg/ai_automation/doctype/ai_pipeline/test_ai_pipeline.py`
- `ai_fr_hg/ai_automation/doctype/ai_pipeline_run/ai_pipeline_run.js`
- `ai_fr_hg/ai_automation/doctype/ai_pipeline_run/ai_pipeline_run.py`
- `ai_fr_hg/ai_automation/doctype/ai_task/ai_task.js`
- `ai_fr_hg/ai_conversation/doctype/ai_agent/ai_agent_list.js`
- `ai_fr_hg/ai_conversation/doctype/ai_agent/test_ai_agent.py`
- `ai_fr_hg/ai_conversation/doctype/ai_conversation/ai_conversation.js`
- `ai_fr_hg/ai_conversation/doctype/ai_conversation/ai_conversation_list.js`
- `ai_fr_hg/ai_conversation/doctype/ai_message/ai_message.js`
- `ai_fr_hg/ai_conversation/doctype/ai_tool/ai_tool.js`
- `ai_fr_hg/ai_conversation/doctype/ai_tool/test_ai_tool.py`
- `ai_fr_hg/ai_core/doctype/ai_execution_log/ai_execution_log.js`
- `ai_fr_hg/ai_core/doctype/ai_folder_favorite/ai_folder_favorite.py`
- `ai_fr_hg/ai_core/doctype/ai_folder_settings/ai_folder_settings.py`
- `ai_fr_hg/ai_core/doctype/ai_folder_settings/test_ai_folder_settings.py`
- `ai_fr_hg/ai_core/doctype/ai_model/ai_model.js`
- `ai_fr_hg/ai_core/doctype/ai_model/ai_model.json`
- `ai_fr_hg/ai_core/doctype/ai_model/ai_model.py`
- `ai_fr_hg/ai_core/doctype/ai_model/test_ai_model.py`
- `ai_fr_hg/ai_core/doctype/ai_platform_settings/ai_platform_settings.json`
- `ai_fr_hg/ai_core/doctype/ai_platform_settings/ai_platform_settings.py`
- `ai_fr_hg/ai_core/doctype/ai_platform_settings/test_ai_platform_settings.py`
- `ai_fr_hg/ai_core/doctype/ai_prompt_template/ai_prompt_template.js`
- `ai_fr_hg/ai_core/doctype/ai_provider/test_ai_provider.py`
- `ai_fr_hg/ai_core/page/ai_assistant/ai_assistant.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document.json`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document.py`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document_list.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document_tree.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/test_ai_document.py`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/test_ai_document_tree.py`
- `ai_fr_hg/ai_knowledge/doctype/ai_document_chunk/ai_document_chunk.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_extraction_schema/ai_extraction_schema.json`
- `ai_fr_hg/ai_knowledge/doctype/ai_knowledge_base/ai_knowledge_base.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_pattern_entity/ai_pattern_entity.py`
- `ai_fr_hg/ai_knowledge/doctype/ai_pattern_entity/test_ai_pattern_entity.py`
- `ai_fr_hg/ai_knowledge/doctype/ai_search_query/ai_search_query.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_translation/ai_translation.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_translation/ai_translation.json`
- `ai_fr_hg/ai_knowledge/doctype/ai_translation/ai_translation_list.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_translation/test_ai_translation.py`
- `ai_fr_hg/ai_knowledge/page/knowledge_explorer/knowledge_explorer.js`
- `ai_fr_hg/ai_learning/doctype/ai_knowledge_candidate/ai_knowledge_candidate.py`
- `ai_fr_hg/ai_learning/doctype/ai_knowledge_candidate/ai_knowledge_candidate_list.js`
- `ai_fr_hg/ai_learning/doctype/ai_knowledge_candidate/test_ai_knowledge_candidate.py`
- `ai_fr_hg/ai_learning/doctype/ai_memory/ai_memory.js`
- `ai_fr_hg/ai_learning/doctype/ai_memory/ai_memory_list.js`
- `ai_fr_hg/ai_learning/doctype/ai_skill/ai_skill.js`
- `ai_fr_hg/ai_learning/doctype/ai_skill/ai_skill_list.js`
- `ai_fr_hg/ai_learning/report/learning_activity/learning_activity.js`
- `ai_fr_hg/ai_learning/report/learning_activity/learning_activity.py`
- `ai_fr_hg/ai_learning/report/memory_usage/memory_usage.js`
- `ai_fr_hg/ai_learning/report/memory_usage/memory_usage.py`
- `ai_fr_hg/ai_learning/report/skill_summary/skill_summary.js`
- `ai_fr_hg/ai_learning/report/skill_summary/skill_summary.py`
- `ai_fr_hg/ai_operations/doctype/ai_audit_log/ai_audit_log.js`
- `ai_fr_hg/ai_operations/doctype/ai_resource_policy/ai_resource_policy.js`
- `ai_fr_hg/ai_operations/doctype/ai_service_health_log/ai_service_health_log.js`
- `ai_fr_hg/ai_operations/doctype/ai_usage_snapshot/ai_usage_snapshot.js`
- `ai_fr_hg/ai_operations/page/ai_model_manager/ai_model_manager.js`
- `ai_fr_hg/api/admin.py`
- `ai_fr_hg/api/chat.py`
- `ai_fr_hg/api/document_tree.py`
- `ai_fr_hg/api/folders.py`
- `ai_fr_hg/api/knowledge.py`
- `ai_fr_hg/hooks.py`
- `ai_fr_hg/install.py`
- `ai_fr_hg/patches.txt`
- `ai_fr_hg/patches/v0_0_14_disable_unsupported_controls.py`
- `ai_fr_hg/patches/v0_0_3_fix_learning_doctype_modules.py`
- `ai_fr_hg/patches/v0_0_4_folder_organization.py`
- `ai_fr_hg/patches/v0_0_5_normalize_legacy_long_int_values.py`
- `ai_fr_hg/patches/v0_0_9_ai_document_tree_organization.py`
- `ai_fr_hg/public/js/ai_helpers.js`
- `ai_fr_hg/public/js/desk_guard.js`
- `ai_fr_hg/public/js/file_folder.js`
- `ai_fr_hg/public/js/file_list.js`
- `ai_fr_hg/tests/test_document_tree_units.py`
- `ai_fr_hg/tests/test_folder_units.py`
- `ai_fr_hg/tests/test_pattern_units.py`
- `ai_fr_hg/tests/test_phase_0_contracts.py`
- `ai_fr_hg/tests/test_units.py`
- `ai_fr_hg/utils/file_hooks.py`
- `ai_fr_hg/utils/permissions.py`
- `docs/API.md`
- `docs/ARCHITECTURE.md`
- `docs/ARCHITECTURE_DECISIONS.md`
- `docs/CONFIGURATION.md`
- `docs/DEVELOPMENT_PLAN.md`
- `docs/EXTENDING.md`
- `docs/FILE_TO_ANSWER.md`
- `docs/GAP_REGISTER.md`
- `docs/LEARNING.md`
- `docs/PROJECT_STATUS.md`
- `docs/TRANSLATION.md`
- `docs/phase-reports/PHASE_0.md`
- `docs/phase-reports/PHASE_0_WORKFLOW.patch.gz`
- `pyproject.toml`

</details>

### Architecture

- Public APIs were not expanded and no business logic was moved into API facades.
- Unsupported-value enforcement lives in the owning DocType controllers.
- Runtime model discovery remains in the monitoring service and now refuses to create a role the engine cannot execute.
- Reader support remains in the canonical reader registry.
- Learning reports use Frappe Query Builder with parameterized conditions instead of interpolated report SQL.
- Background document-tree jobs rely on Frappe's native worker transaction commit/rollback rather than manual commits.
- Frappe File remains the sole physical folder tree authority.
- Accepted boundaries and evaluated Frappe alternatives are recorded in `ARCHITECTURE_DECISIONS.md`.
- No duplicate encryption, folder, version, reranker, migration, or CI framework was introduced.

### Frappe v17 integration

- CI resolves upstream `develop`, then verifies immutable Frappe revision `d7000da3d5862087d3df08e009fe76518ea649c4`, whose package version is `17.0.0-dev`.
- Toolchain matches upstream v17 development requirements: Python 3.14, Node 24, MariaDB 11.8.
- CI uses Bench initialization, site creation, app install, `migrate`, app asset build, and `bench run-tests`.
- Desk exposure uses native DocType JSON metadata, not JavaScript-only hiding.
- Upgrade behavior uses an idempotent post-model-sync Frappe patch.
- Folder scope uses native Frappe File rather than a competing folder DocType/source model.

### Security

- A caller cannot persist `encrypt_documents = 1` through the settings controller.
- Migration removes stale false encryption state without altering document content.
- Unsupported Folder and Reranker values are rejected by backend controllers, independent of the UI.
- Workflow token permissions are read-only.
- Semgrep uses `--no-suppress-errors`; a missing/failed ruleset cannot silently pass. Tool and Frappe-rule revisions are immutable.
- All 76 Frappe Semgrep findings from the first executing hosted run were dispositioned: reports now use Query Builder/translated labels, RPC arguments and messages are typed/translated, worker transactions use Frappe ownership, and narrowly annotated calls document manually reviewed dynamic identifiers, capability probes, external-effect commits, and requester-authority boundaries.
- Translation execution now validates its durable requester, checks write permission after restoring that requester, and restores the previous worker identity even after provider failure.
- Dependency collection uses strict mode and audits installed optional extras in the Python 3.14 job.
- Phase 1 isolation, generic tool permissions, transport hardening, telemetry redaction, and File authorization findings remain open; Phase 0 does not claim those security boundaries are fixed.

### Data/migration

- Added `v0_0_14_disable_unsupported_controls` and registered it once in `patches.txt`.
- The patch is idempotent by construction and by integration regression: it resets a Single DocType flag and repeats stable updates to matching legacy model rows.
- It preserves document/chunk/translation text, AI Documents with historical Folder source values, AI Model identities, and AI Model Version child rows.
- Legacy Reranker rows are disabled and lose default status; they are not deleted or retyped.
- Fresh metadata defaults the dormant encryption field to off.
- **Current migration runtime result:** not executed in this sandbox because no MariaDB/Redis/Bench services are installed and apt package retrieval failed. Hosted migration evidence is still required.

### Frontend

- Encryption, extraction target mapping, and model versions are hidden through DocType metadata.
- Folder and Reranker are removed from Select options, so native forms cannot offer them.
- Translation now labels the actual text-structure behavior and explains the binary-format limitation.
- OCR and translation-memory limitations are visible in settings descriptions.
- All 52 production JavaScript files parse, Prettier passes, and ESLint passes.
- No browser/Desk run was possible; metadata visibility must still be verified on the hosted/real v17 bench.

### Tests

Commands and results executed in this workspace:

- `.venv/bin/pre-commit run --all-files` — **PASS**, all 12 configured hooks (metadata syntax, Ruff import/lint/format, Prettier, ESLint included).
- `python -m unittest discover -s ai_fr_hg/tests -p 'test_phase_0_contracts.py' -v` — **PASS, 13/13**.
- `python -m unittest ai_fr_hg.tests.test_document_tree_units -v` — **PASS, 20/20**.
- `python -m unittest ai_fr_hg.tests.test_folder_units -v` — **PASS, 16/16**.
- `python -m compileall -q ai_fr_hg` — **PASS**.
- JSON parse across the repository — **PASS, 60 files**.
- `find ai_fr_hg -type f -name '*.js' ... node --check` — **PASS, 52 production JavaScript files**.
- `npx --yes yaml-lint .github/workflows/ci.yml .github/workflows/linter.yml` — **PASS**.
- `.venv/bin/pip-audit --strict --desc on .` — **PASS, no known vulnerabilities in the locally resolved base project**. The workflow additionally installs/audits all optional extras on Python 3.14.
- `.venv/bin/semgrep scan --config /tmp/frappe-semgrep-rules/rules --error --metrics=off` — **PASS, 49 rules over 321 tracked targets, zero findings** after correcting the initial 76 findings.
- The additional registry ruleset `r/python.lang.correctness` remains locally unavailable because `semgrep.dev` closes TLS; strict error behavior was separately verified and hosted execution is required.
- `git diff --check` — **PASS**.
- Audit/register comparison — **PASS, 79 unique audit IDs and 79 unique registered IDs**.

Additional checks attempted:

- Semgrep installed and local Frappe rules cloned, but `semgrep.dev` closed TLS while resolving `r/python.lang.correctness`. The workflow now uses `--no-suppress-errors`, so this condition will correctly fail the hosted `Linter` status rather than be reported green.
- A push attempt after the reported reauthorization was still rejected: the connected `arena-ai-coding-agent[bot]` GitHub App cannot create or update `.github/workflows/ci.yml` without workflow permission. The final workflow-hardening commit is therefore not present on the remote branch.
- `python -m unittest ai_fr_hg.tests.test_pattern_units -v` could not import `frappe` in the system-Python environment. This test module remains covered by the Bench suite; it is not counted as a local pass.
- A local real bench setup was attempted, but Debian package repositories were unreachable and MariaDB/Redis packages were unavailable. No current full Frappe suite result is claimed.

### Hosted execution

- Pull request [#30](https://github.com/Oscar-Hyde/ai_fr_hg/pull/30) is open from the session branch.
- The earlier zero-step billing failures are retained as historical evidence, but billing is now operational: push runs CI `32285843903` and Quality `32285843900` executed real steps at `cfb9a90aa3a1d9f3fe42d759bcf6d3978d2ff0b1`.
- After correcting all 76 Frappe Semgrep findings, PR Quality run `32287915332` at `755131b84260eec96e823a202b60c363b1969f85` passed all three jobs: `Linter` in 1m6s, `Frontend static` in 8s, and `Dependency audit` in 24s.
- PR Server run `32287915407` still failed during pinned-bench initialization because the remote branch necessarily retains the old workflow. Python, Node, MariaDB, Redis, and system setup succeeded before the immutable-SHA fetch used nonexistent Frappe remote `origin`.
- The final local workflow uses Bench's canonical `upstream` remote, pins the pre-commit/Semgrep/rules revisions, enables strict Semgrep error handling, and audits all optional dependency extras. GitHub still rejects that commit because `arena-ai-coding-agent[bot]` lacks workflow-file permission.
- The exact three-file workflow/contract diff is uploaded as `docs/phase-reports/PHASE_0_WORKFLOW.patch.gz`. A repository owner can apply it from PR #30's branch with `gzip -dc docs/phase-reports/PHASE_0_WORKFLOW.patch.gz | git apply`, review, commit, and push using owner credentials.

### Runtime verification

Hosted Server run `32325110514` on `main` at `a82ef4b491ed0607b2ddcc6b37df2a968ef2d227` (merge of PR #30) **succeeded** after pinned Frappe v17 bench init, app install, migrate, asset build, and `bench run-tests --app ai_fr_hg`. That is current real-bench evidence for the merged Phase 0 product/code baseline. Hosted Quality run `32325110520` on the same SHA failed: Semgrep Cloud registry (`r/python.lang.correctness`) and editable-install `pip-audit`. The owner-applyable `PHASE_0_QUALITY_GATE.patch.gz` removes those two non-executable dependencies. Branch protection remains off (`main.protected: false`); the GitHub App received HTTP 403 on the branch-protection API.

### Mandatory phase review

**Architecture review**

- SRP and UI → API → service/core → DocType/ORM separation are preserved.
- Canonical File/reader/model/controller ownership is preserved.
- No Frappe responsibility was duplicated; custom behavior is limited to domain validation and provider/reader discovery where Frappe has no equivalent.
- Formatting baseline changes are mechanical and required by the configured gate.

**Security review**

- False encryption state and unsupported direct values are rejected server-side.
- No new privilege elevation, background authority, unbounded API, or external provider path was introduced.
- Phase 1 security gaps remain explicit and block production.

**Data review**

- Migration is additive/idempotent and preserves historical records/content.
- No index/constraint is needed for exposure removal.
- Upgrade and fresh-install execution remain unverified until a real bench job runs.

**Frontend review**

- Native metadata reflects backend reality; no UI-only permission control was added.
- Static JS quality passes.
- Real Desk v17 visibility/accessibility remains unverified.

**Test review**

- New tests cover metadata contracts, backend rejection, model discovery, supported-format output, migration idempotency/preservation, audit-register completeness, and CI contract.
- Existing lint debt was fixed rather than ignored globally; only intentional multilingual fixture files/lines have targeted Unicode-confusable annotations.
- Hosted Frappe integration, migration, and browser behavior remain missing and therefore block a pass.

### Remaining issues

1. The connected `arena-ai-coding-agent[bot]` still lacks workflow-file permission. Owner-authorized patches: `PHASE_0_WORKFLOW.patch.gz`, `PHASE_0_DEPENDENCY_AUDIT.patch.gz`, and `PHASE_0_QUALITY_GATE.patch.gz` (2026-08-20: replace Semgrep Cloud `r/python.lang.correctness` with pinned Frappe `semgrep scan`, and audit declared extras via `pip-audit==2.10.1 --requirement`). Apply Quality gate with `gzip -dc docs/phase-reports/PHASE_0_QUALITY_GATE.patch.gz | git apply`.
2. The corrected code passes the remote Quality workflow, but the repaired Server workflow and final strict/pinned quality workflow have not executed; all four required checks must pass on the final SHA.
3. The repository is now public, removing the prior plan restriction, but `main` remains unprotected. An attempt to require pull requests and all four statuses returned HTTP 403 because the Arena GitHub App lacks branch-administration permission.
4. No current real-bench install/migrate/test result exists for this patchset.
5. No real Desk/browser verification exists for the metadata changes.
6. Upstream stable Frappe v17 does not exist; the reproducible target is an immutable `17.0.0-dev` revision.
7. All findings still marked `OPEN` in `GAP_REGISTER.md` remain unresolved. Phase 1 has not started.

## Phase verdict

`FAIL`

Hosted **Server**, **Linter**, **Frontend static**, and **Dependency audit** all passed on PR #31 SHA `61d19263ff60942532f535f29a70794671e4afcd` (Quality run `32328156242`, Server run `32328156228`). The only remaining Phase 0 gate is **branch protection on `main`**, which this GitHub App cannot enable (HTTP 403). Phase 1 must not begin until the repository owner requires those four statuses on `main`.

## Verification addendum — 2026-08-20

Closed-phase re-inspection of Phases 0–2 (see `PHASE_0_2_VERIFICATION.md`).

- The four required checks remain green on `main` (Phase 1 merge) and on PR #33 (Phase 2, SHA `a798663`).
- `main.protected` is still `false`. OPS-01 remains **BLOCKED**. The Phase 0 verdict is unchanged: **FAIL** solely because branch protection is owner-only.
- Product-truth drift in `README.md`, `docs/PROJECT_STATUS.md`, `docs/TRANSLATION.md`, `docs/ARCHITECTURE.md`, and `docs/FILE_TO_ANSWER.md` was corrected so closed Phase 0/1/2 findings are no longer described as open.
- Subsequent phases (1 and 2) were already executed on this branch after the original FAIL, with documented non-blocking limitation. This addendum does not reopen them.
