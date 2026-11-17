from __future__ import annotations

import argparse

from src.preprocess.text_chunker_v2 import build_chunks_v2
from src.retrieval.bge_m3_indexer import load_tokenizer


def main() -> None:
    parser = argparse.ArgumentParser(description="BGE-M3용 토큰 기반 공시 청킹 v2")
    parser.add_argument("--corp-codes", nargs="*", help="지정한 기업코드만 증분 청킹")
    args = parser.parse_args()
    result = build_chunks_v2(
        load_tokenizer(), corp_codes=set(args.corp_codes) if args.corp_codes else None
    )
    print(f"청킹 v2 완료: {result}")


if __name__ == "__main__":
    main()
