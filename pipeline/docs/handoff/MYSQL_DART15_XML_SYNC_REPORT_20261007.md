# MySQL 15개사·DART 원본 XML 동기화 결과

- 실행일: 2026-10-07
- 운영 데이터 버전: `2026-10-07-dart15-xml-v1`
- 상태: 검증 완료, `is_current=1`
- MySQL: 8.0.46 / `dart_big_in`

## 최종 건수

| 항목 | 건수 |
|---|---:|
| 기업 | 15 |
| 재무 | 33,386 |
| CAPEX | 349 |
| 공시 | 175 |
| BGE-M3 청크 | 70,663 |
| 임베딩 | 70,663 |
| 태그 보존 원본 XML | 251 |
| 청크→원본 문서 매핑 | 70,663 |

## 검증 결과

- 기존 반도체 5개사의 재무·CAPEX·공시·청크 업무 컬럼 체크섬 일치
- 기존 임베딩 21,797개 BLOB 체크섬 일치
- SQLite 임베딩 70,663개와 FAISS 전체 벡터 바이트 일치
- 원본 XML 파일과 MySQL BLOB 251개 SHA-256·byte size 일치
- 모든 활성 청크가 `rcept_no + source_path`가 같은 원본 문서 하나에 연결
- 중복 chunk_id/vector_id 0, 고아 매핑 0
- KRX_SEMI 5, KRX_HEALTH 5, KRX_STEEL 5, KRX_BANK 없음
- 읽기 전용 `dart_backend` 계정의 신규 테이블 조회 확인
- Python 전체 단위 테스트 57개 통과

## 백업

변경 전 전체 논리 백업:

- 경로: `backups/mysql/20261007_pre_dart15_xml/dart_big_in_pre_dart15_xml.sql`
- 크기: 282,638,295 bytes
- SHA-256: `53be66ee58472fd4d10f8c715f80364f94b503ae17009a4d2e017fbce2b760de`

백업 폴더는 Git에서 제외되며 이 PC 로컬에 보존된다.

## 백엔드 인수인계

신규 스키마와 조회 SQL은 [MYSQL_XML_SOURCE_HANDOFF.md](MYSQL_XML_SOURCE_HANDOFF.md)에 정리되어 있다.
DB 작업 범위는 원본 XML 보존과 `chunk_id → document_id → raw_xml` 조회 기반까지다.
XML→HTML 변환 및 화면 렌더링은 백엔드/UI 계층에서 구현한다.
