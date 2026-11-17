# 백엔드 조회 예제 (2026-10-11)

- 대상: 중앙 MySQL `dart_big_in` (백엔드 계정 `dart_backend`, 읽기 전용)
- 기준 버전: 재무 `2026-10-11-dart25-2026q1h1-v14`, 인덱스 `bge-m3-2026-10-11-dart25-2026q1h1-v14`
- 아래 SQL은 모두 2026-10-11 승격 직후 운영 MySQL에서 실제로 실행했고, 결과 행 수와 첫 행을 함께 적었다.
- 버전 관리 테이블(`financial_facts`, `capex_facts`, `filings`, `chunk_metadata`, `chunk_embeddings`,
  `filing_source_documents`)은 항상 `is_active = 1` 조건을 붙인다. `financial_metric_values`에는 버전 컬럼이 없다.
- KPI 비율 단위는 BPS(1 = 0.01%)다. `value / 100`이 퍼센트다.
- 키 의미와 연결 규칙: `BACKEND_DATA_CONTRACT_20261011.md`

## 1. 기업 목록 (산업 그룹·프로필 포함)

```sql
SELECT c.corp_code, c.corp_name, c.stock_code, k.index_code AS industry_group,
       -- 프로필 행이 없는 기업은 KRX 그룹으로 산업을 정한다(서비스 get_industry_kpis와 같은 규칙)
       COALESCE(p.industry_profile, CASE k.index_code WHEN 'KRX_SEMI' THEN 'SEMICONDUCTOR'
                WHEN 'KRX_AUTO' THEN 'AUTOMOTIVE' WHEN 'KRX_STEEL' THEN 'STEEL'
                WHEN 'KRX_HEALTH' THEN 'HEALTHCARE' END) AS industry_profile,
       COALESCE(p.preferred_fs_div, 'CFS') AS preferred_fs_div
FROM companies c
LEFT JOIN krx_index_constituents k ON k.corp_code = c.corp_code AND k.is_current = 1
LEFT JOIN company_financial_profiles p ON p.corp_code = c.corp_code
ORDER BY k.index_code, c.corp_name;
```

결과: 25행. 첫 행: `01042775 · HL만도 · 204320 · KRX_AUTO · AUTOMOTIVE · CFS`

## 2. 기업별 2026 Q1/H1 재무제표 값 (표준 계정 VIEW)

```sql
SELECT corp_name, year, report_type, fs_div, standard_account, value_type, value, rcept_no
FROM standardized_financials
WHERE corp_code = '00126380' AND year = 2026 AND report_type IN ('Q1', 'H1')
  AND standard_account IN ('매출액', '영업이익', '당기순이익', '자산총계')
ORDER BY report_type, standard_account, value_type;
```

결과: 14행. 첫 행: `삼성전자 · 2026 · H1 · CFS · 당기순이익 · cumulative · 118849733000000 · 20260814003699`

## 2b. 기업별 2026 Q1/H1 원천 계정 값 (financial_facts)

```sql
SELECT f.year, f.report_type, f.fs_div, f.sj_div, f.account_id, f.original_account_nm, f.value_type,
       CASE WHEN f.is_calculated = 1 THEN f.calculated_value ELSE f.normalized_value END AS value,
       f.is_calculated, f.rcept_no
FROM financial_facts f
WHERE f.is_active = 1 AND f.corp_code = '00126380' AND f.year = 2026
  AND f.account_id IN ('ifrs-full_Revenue', 'dart_OperatingIncomeLoss', 'ifrs-full_ProfitLoss')
ORDER BY f.report_type, f.account_id, f.value_type;
```

결과: 22행. 첫 행: `2026 · Q1 · CFS · IS · dart_OperatingIncomeLoss · 영업이익 · cumulative · 57232797000000 · 0 · 20260515002181`

## 3. 기업별 KPI (2026 H1)

```sql
SELECT v.metric_code, d.metric_name, v.value_type, v.value, v.unit, v.fs_div, v.scope,
       v.source_kind, v.review_status, v.source_rcept_no
FROM financial_metric_values v
JOIN financial_metric_definitions d ON d.metric_code = v.metric_code
WHERE v.corp_code = '00164742' AND v.year = 2026 AND v.report_type = 'H1'
ORDER BY v.metric_code, v.value_type;
```

결과: 18행. 첫 행: `CAPEX · 설비투자(CAPEX) · cumulative · 4003527000000 · KRW · CFS · consolidated · DIRECT · approved · 20260814003521`

## 4. 산업별 KPI 비교 (반도체 그룹 2026 H1 영업이익률, 분기 단독)

```sql
SELECT c.corp_name, v.value AS operating_margin_bps, v.value / 100 AS operating_margin_pct, v.review_status
FROM krx_index_constituents k
JOIN companies c ON c.corp_code = k.corp_code
JOIN financial_metric_values v ON v.corp_code = k.corp_code
WHERE k.index_code = 'KRX_SEMI' AND k.is_current = 1
  AND v.year = 2026 AND v.report_type = 'H1' AND v.metric_code = 'OPERATING_MARGIN' AND v.value_type = 'quarterly'
ORDER BY v.value DESC;
```

결과: 5행. 첫 행: `SK하이닉스 · 7633 · 76.3300 · approved`

## 5. CAPEX (확정값만: cash_outflow_amount IS NOT NULL)

```sql
SELECT c.corp_name, x.year, x.report_type, x.value_type, x.cash_outflow_amount, x.review_required,
       x.capex_method, x.rcept_no
FROM capex_facts x
JOIN companies c ON c.corp_code = x.corp_code
WHERE x.is_active = 1 AND x.year = 2026 AND x.cash_outflow_amount IS NOT NULL
ORDER BY c.corp_name, x.report_type, x.value_type;
```

결과: 80행. 첫 행: `HLB · 2026 · Q1 · cumulative · 362990670 · 0 · direct_account · 20260515001567`

## 6. 헬스케어 R&D (원문 근거 포함)

```sql
SELECT c.corp_name, v.year, v.report_type, v.metric_code, v.value, v.unit, v.value_type,
       v.review_status, v.source_rcept_no, v.source_document_id, v.source_chunk_id,
       JSON_UNQUOTE(JSON_EXTRACT(v.source_values, '$.locator.source_path')) AS source_path
FROM financial_metric_values v
JOIN companies c ON c.corp_code = v.corp_code
WHERE v.metric_code IN ('RND_EXPENSE', 'RND_TO_REVENUE') AND v.year = 2026
ORDER BY c.corp_name, v.report_type, v.metric_code;
```

결과: 20행. 첫 행: `HLB · 2026 · Q1 · RND_EXPENSE · 10640182000 · KRW · quarterly · approved · 20260515001567 · c594f795d7dd2217c89be7be3ae72f9c0ed948b3a7038dae481c823fcd63 · 20260515001567:v2:12:27 · data/raw/filings/xml/00199252/2026/Q1/20260515001567/2026051`

## 7. 금융 KPI (구조화 KPI 계층 우선)

```sql
SELECT c.corp_name, p.industry_profile, v.year, v.report_type, v.metric_code, v.value_type, v.value,
       v.unit, v.scope, v.source_kind, v.review_status
FROM financial_metric_values v
JOIN companies c ON c.corp_code = v.corp_code
JOIN company_financial_profiles p ON p.corp_code = v.corp_code
WHERE p.industry_profile IN ('BANK_HOLDING', 'INTERNET_BANK') AND v.year = 2026 AND v.report_type = 'H1'
  AND v.metric_code IN ('NET_INTEREST_INCOME', 'NET_FEE_COMMISSION_INCOME', 'CREDIT_LOSS_EXPENSE')
ORDER BY c.corp_name, v.metric_code, v.value_type;
```

결과: 30행. 첫 행: `KB금융 · BANK_HOLDING · 2026 · H1 · CREDIT_LOSS_EXPENSE · cumulative · 1013014000000 · KRW · holding_consolidated · DIRECT · approved`

## 7b. 금융 비율 KPI 적재 여부 (NIM/BIS/CET1/NPL/연체율 = 0행, Vector 검색 fallback)

```sql
SELECT d.metric_code, d.metric_name, COUNT(v.metric_value_id) AS structured_rows
FROM financial_metric_definitions d
LEFT JOIN financial_metric_values v ON v.metric_code = d.metric_code
WHERE d.metric_code IN ('NIM', 'BIS_CAPITAL_RATIO', 'TOTAL_CAPITAL_RATIO', 'CET1_RATIO', 'NPL_RATIO', 'DELINQUENCY_RATIO')
GROUP BY d.metric_code, d.metric_name
ORDER BY d.metric_code;
```

결과: 6행. 첫 행: `BIS_CAPITAL_RATIO · BIS자기자본비율 · 0`

## 8. review_required 목록 (KPI + CAPEX)

```sql
SELECT 'KPI' AS layer, c.corp_name, v.year, v.report_type, v.metric_code AS item, v.value_type, v.value
FROM financial_metric_values v JOIN companies c ON c.corp_code = v.corp_code
WHERE v.review_status = 'review_required'
UNION ALL
SELECT 'CAPEX', c.corp_name, x.year, x.report_type, 'CAPEX', x.value_type, x.calculated_value
FROM capex_facts x JOIN companies c ON c.corp_code = x.corp_code
WHERE x.is_active = 1 AND x.review_required = 1
ORDER BY layer, corp_name, year, report_type;
```

결과: 12행. 첫 행: `CAPEX · HLB · 2023 · H1 · CAPEX · quarterly · -2587175412`

## 9. rcept_no 기준 조회 (공시 1건의 재무·KPI·청크 규모)

```sql
SELECT f.rcept_no, f.corp_code, f.year, f.report_type, f.rcept_dt, f.dart_report_name, f.is_amended,
       (SELECT COUNT(*) FROM financial_facts x WHERE x.rcept_no = f.rcept_no AND x.is_active = 1) AS facts,
       (SELECT COUNT(*) FROM financial_metric_values v WHERE v.source_rcept_no = f.rcept_no) AS kpis,
       (SELECT COUNT(*) FROM chunk_metadata m WHERE m.rcept_no = f.rcept_no AND m.is_active = 1) AS chunks,
       (SELECT COUNT(*) FROM filing_source_documents d WHERE d.rcept_no = f.rcept_no AND d.is_active = 1) AS documents
FROM filings f
WHERE f.rcept_no = '20260814003699' AND f.is_active = 1;
```

결과: 1행. 첫 행: `20260814003699 · 00126380 · 2026 · H1 · 2026-08-14 · NULL · NULL · 194 · 20 · 662 · 1`

## 10. source_document_id 기준 원문 조회 (raw_xml은 원본 바이트)

```sql
SELECT d.document_id, d.rcept_no, d.source_path, d.byte_size, d.sha256, d.detected_encoding,
       LENGTH(d.raw_xml) AS raw_xml_bytes
FROM filing_source_documents d
WHERE d.document_id = (SELECT v.source_document_id FROM financial_metric_values v
                       WHERE v.corp_code = '00413046' AND v.year = 2026 AND v.report_type = 'H1'
                         AND v.metric_code = 'RND_EXPENSE' LIMIT 1);
```

결과: 1행. 첫 행: `7191efffb33c320e474ea831402b13f64a7f3c5eb55386d5c64c90e110f7 · 20260814003751 · data/raw/filings/xml/00413046/2026/H1/20260814003751/2026081 · 6799727 · 55d7deff45ca89d4b8f7f54a33602a9424985c50a15726abe0d7c57a9779 · utf-8 · 6799727`

## 11. chunk_id 기준 청크 조회 → 원문 문서 → 공시

```sql
SELECT m.chunk_id, m.vector_id, m.corp_name, m.year, m.report_type, m.rcept_no,
       m.section_name, m.subsection_name, LEFT(m.original_text, 80) AS text_head,
       s.document_id, d.source_path, f.dart_report_name
FROM chunk_metadata m
JOIN chunk_source_documents s ON s.chunk_id = m.chunk_id
JOIN filing_source_documents d ON d.document_id = s.document_id
JOIN filings f ON f.rcept_no = m.rcept_no
WHERE m.chunk_id = (SELECT source_chunk_id FROM financial_metric_values
                    WHERE corp_code = '00413046' AND year = 2026 AND report_type = 'H1'
                      AND metric_code = 'RND_EXPENSE' LIMIT 1);
```

결과: 1행. 첫 행: `20260814003751:v2:13:15 · 165485 · 셀트리온 · 2026 · H1 · 20260814003751 · II. 사업의 내용 · 6. 주요계약 및 연구개발활동 · 9 합계 71 392 349 812 (3) 핵심 연구인력 직위 성명 담당업무 주요경력 비고 사장 이상준 데이 · 7191efffb33c320e474ea831402b13f64a7f3c5eb55386d5c64c90e110f7 · data/raw/filings/xml/00413046/2026/H1/20260814003751/2026081 · NULL`

## 12. 신규 2026 데이터만 조회 (슬롯별 커버리지)

```sql
SELECT cv.corp_code, c.corp_name, cv.report_type, cv.rcept_no, cv.financial_exists, cv.filing_exists,
       cv.chunk_exists, cv.embedding_exists, cv.status
FROM coverage cv
JOIN companies c ON c.corp_code = cv.corp_code
WHERE cv.year = 2026
ORDER BY c.corp_name, cv.report_type;
```

결과: 50행. 첫 행: `00199252 · HLB · Q1 · 20260515001567 · 1 · 1 · 1 · 1 · PASS`

## 12b. 2026 신규 데이터 규모

```sql
SELECT 'financial_facts' AS t, COUNT(*) AS n FROM financial_facts WHERE year = 2026 AND is_active = 1
UNION ALL SELECT 'capex_facts', COUNT(*) FROM capex_facts WHERE year = 2026 AND is_active = 1
UNION ALL SELECT 'financial_metric_values', COUNT(*) FROM financial_metric_values WHERE year = 2026
UNION ALL SELECT 'filings', COUNT(*) FROM filings WHERE year = 2026 AND is_active = 1
UNION ALL SELECT 'chunk_metadata', COUNT(*) FROM chunk_metadata WHERE year = 2026 AND is_active = 1;
```

결과: 5행. 첫 행: `financial_facts · 11373`

## 13. 현재 버전 확인 (재무 버전과 인덱스 버전은 독립)

```sql
SELECT 'financial' AS axis, data_version AS version, validated, is_current FROM data_versions WHERE is_current = 1
UNION ALL
SELECT 'embedding', index_version, NULL, is_current FROM embedding_metadata WHERE is_current = 1;
```

결과: 2행. 첫 행: `financial · 2026-10-11-dart25-2026q1h1-v14 · 1 · 1`
