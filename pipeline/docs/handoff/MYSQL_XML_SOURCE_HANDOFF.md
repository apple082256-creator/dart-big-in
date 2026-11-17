# MySQL DART 원본 XML 인수인계

## 범위

MySQL 운영 버전 `2026-10-07-dart20-auto-xml-v1`에는 20개사·235개 공시와 태그가 보존된
원본 XML 329개가 저장되어 있다. `chunk_source_documents`의 모든 운영 청크 97,097개는
정확히 한 원본 문서에 연결된다.

DB 계층은 원본 바이트와 연결 관계를 제공한다. XML→안전한 HTML 변환, 표 렌더링,
선택 위치 강조와 화면 구성은 백엔드/UI 계층의 책임이다.

## 신규 테이블

### `filing_source_documents`

- PK: `document_id`
- 문서 소속: `rcept_no`
- 한 공시 안의 문서 구분: `document_sequence`, `source_path`
- 원본 검증: `byte_size`, `sha256`
- 원본 바이트: `raw_xml LONGBLOB`
- UNIQUE: `(rcept_no, source_path)`, `(rcept_no, document_sequence)`
- FK: `rcept_no → filings.rcept_no`

`document_id`는 `SHA-256(rcept_no + NUL + source_path)`이다. `raw_xml`은 파일을 디코딩하거나
개행을 바꾸지 않은 바이트이며, `detected_encoding`은 렌더러를 위한 참고 메타데이터다.

### `chunk_source_documents`

- PK/FK: `chunk_id → chunk_metadata.chunk_id`
- FK: `document_id → filing_source_documents.document_id`
- 현재 매핑 방식: `source_path_exact`

별도 매핑 테이블을 사용하므로 기존 `chunk_metadata.original_text`, `chunk_text`, `chunk_id`,
`vector_id`는 변경하지 않았다.

## 백엔드 조회 예시

### chunk_id로 원본 문서 메타데이터 조회

```sql
SELECT c.chunk_id, c.rcept_no, c.section_name, c.subsection_name,
       d.document_id, d.document_sequence, d.file_name, d.mime_type,
       d.detected_encoding, d.byte_size, d.sha256
FROM chunk_metadata c
JOIN chunk_source_documents m ON m.chunk_id = c.chunk_id
JOIN filing_source_documents d ON d.document_id = m.document_id
WHERE c.chunk_id = ? AND c.is_active = 1 AND d.is_active = 1;
```

### 원본 XML 바이트 조회

```sql
SELECT document_id, mime_type, detected_encoding, byte_size, sha256, raw_xml
FROM filing_source_documents
WHERE document_id = ? AND is_active = 1;
```

DB 드라이버가 반환한 `raw_xml`은 문자열로 강제 변환하지 말고 bytes/BLOB으로 받아야 한다.

### 한 공시의 원본 문서 목록

```sql
SELECT document_id, document_sequence, file_name, mime_type, byte_size, sha256
FROM filing_source_documents
WHERE rcept_no = ? AND is_active = 1
ORDER BY document_sequence;
```

### chunk_id에서 원본 XML까지 한 번에 조회

```sql
SELECT d.document_id, d.rcept_no, d.file_name, d.mime_type,
       d.detected_encoding, d.byte_size, d.sha256, d.raw_xml
FROM chunk_source_documents m
JOIN filing_source_documents d ON d.document_id = m.document_id
WHERE m.chunk_id = ? AND d.is_active = 1;
```

## 무결성 확인

```sql
SELECT COUNT(*) FROM filing_source_documents WHERE is_active = 1; -- 329
SELECT COUNT(*) FROM chunk_source_documents;                       -- 97097

SELECT COUNT(*) AS broken_links
FROM chunk_source_documents m
JOIN chunk_metadata c ON c.chunk_id = m.chunk_id
JOIN filing_source_documents d ON d.document_id = m.document_id
WHERE c.rcept_no <> d.rcept_no OR c.source_file <> d.source_path; -- 0
```

파일 원본과 DB BLOB은 각각 SHA-256을 계산해 `sha256`과 대조한다. 운영 검증 명령:

```bash
.venv/bin/python infra/mysql/validate_dart15_xml_mysql.py \
  --data-version 2026-10-07-dart20-auto-xml-v1
```

## 재적재와 rollback

`sync_source_documents_to_mysql.py`는 동일 자연키를 upsert하므로 재실행해도 중복되지 않는다.
적재 전에는 반드시 MySQL 전체 논리 백업을 만들고 SHA-256을 기록한다. 현재 작업 직전 백업은
`backups/mysql/20261007_pre_dart20_auto_xml/`에 있다.

원본 XML만으로 표 태그와 셀 병합 구조는 제공할 수 있지만 DART 화면과 완전히 동일한 이미지·CSS가
필요하면 원본 ZIP의 부속 이미지, XSLT, CSS를 별도로 수집해야 한다.
