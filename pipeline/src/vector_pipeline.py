from __future__ import annotations

from src.retrieval.vector_retriever import VECTOR_DB_PATH, build_vector_database


def main() -> None:
    result = build_vector_database()
    print(
        f"완료: {result['files']}개 청크 파일, {result['inserted']:,}개 벡터 적재, "
        f"{result['skipped']:,}개 품질 제외, {result['duplicates']:,}개 중복 제외, "
        f"{result['ignored_other_companies']:,}개 비대상기업 청크 무시\n"
        f"Vector DB: {VECTOR_DB_PATH}"
    )


if __name__ == "__main__":
    main()
