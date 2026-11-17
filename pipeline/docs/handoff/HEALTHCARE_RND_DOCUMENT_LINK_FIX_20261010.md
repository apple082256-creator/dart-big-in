# Healthcare R&D document-link and vector-version fix (2026-10-10)

## Result

- Current financial data version: `2026-10-10-healthcare-rnd-docid-v13`
- Current embedding version: `bge-m3-2026-10-09-dart25-h1h3-v11`
- The two version axes are intentionally independent. Financial-only releases
  no longer invalidate or rename the unchanged embedding cache.
- All 120 `RND_EXPENSE` / `RND_TO_REVENUE` rows now store the canonical
  64-character `filing_source_documents.document_id` in `source_document_id`.
- The readable XML path remains in `source_values.locator.source_path`.
- Direct metric -> document and metric -> chunk -> document joins pass 120/120.

## Code changes

- `src/healthcare_rnd_pipeline.py`: generate the same SHA-256 document ID as
  `filing_source_documents` instead of storing a path as an ID.
- `infra/mysql/team_client/dart_semantic_search.py`: use the unique current
  `embedding_metadata.index_version` as the vector cache key instead of the
  current financial data version.
- Added regression coverage in `tests/test_healthcare_rnd.py` and
  `tests/test_mysql_semantic_version.py`.

## Backups and hashes

- SQLite rollback snapshot:
  `db/archive/financial_before_healthcare_docid_20261010.db`
  - SHA-256: `22704d77411984210ae22044e448c4023a31adc2a3c277ce7edcd83576881bed`
- MySQL rollback dump:
  `db/archive/mysql_dart_big_in_before_healthcare_docid_20261010.sql`
  - SHA-256: `dd5468fcf296c6bb362a948158950b9707b7cf861cdebad7e3872d028fee63e3`
- Promoted SQLite SHA-256:
  `8979cc618f91a30cdfd937b4417ebf45f2d0c533b05cc524cdd38976df035ea1`

The MySQL dump contains the changed `data_versions`,
`financial_metric_values`, and `sync_runs` data. Restore it only after taking a
fresh backup of the then-current database and stopping writers. The SQLite
rollback is an atomic replacement with the snapshot after an integrity check.

## Verification

- SQLite integrity: `ok`
- R&D coverage: 60 expense + 60 ratio, all approved
- Review required / missing / natural-key duplicates: 0 / 0 / 0
- Local/MySQL equality over all 20 metric fields: 120/120
- Active financial facts / CAPEX facts: 63,931 / 469
- Active embeddings unchanged: 147,720
- Current data version count: 1
- Python compile and `git diff --check`: pass
- Healthcare, semantic version, operational retriever, quarterly trend, and
  industry KPI regression tests: pass
