-- Document-extracted KPI rows may use the collected XML path as the locator.
-- Paths are longer than a SHA-256 document id, so preserve them without truncation.
ALTER TABLE financial_metric_values
  MODIFY COLUMN source_document_id VARCHAR(255) NULL;
