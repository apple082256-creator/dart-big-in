-- Document-extracted KPI rows may use the collected XML path as the locator.
ALTER TABLE financial_metric_values
  MODIFY COLUMN source_document_id VARCHAR(255) NULL;
