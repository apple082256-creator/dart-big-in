from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


MODULE_PATH = Path("infra/mysql/team_client/dart_semantic_search.py").resolve()
CLIENT_PATH = MODULE_PATH.parent
if str(CLIENT_PATH) not in sys.path:
    sys.path.insert(0, str(CLIENT_PATH))
fake_client = types.ModuleType("dart_db_client")
fake_client.query = lambda *args, **kwargs: None
sys.modules.setdefault("dart_db_client", fake_client)
SPEC = importlib.util.spec_from_file_location("dart_semantic_search_tested", MODULE_PATH)
semantic = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(semantic)


class SemanticEmbeddingVersionTests(unittest.TestCase):
    def test_current_version_comes_from_embedding_metadata(self) -> None:
        frame = pd.DataFrame({"index_version": ["bge-m3-vector-v11"]})
        with patch.object(semantic, "query", return_value=frame) as query:
            self.assertEqual(semantic.current_embedding_version(), "bge-m3-vector-v11")
        self.assertIn("embedding_metadata", query.call_args.args[0])

    def test_cache_key_uses_embedding_version_not_financial_version(self) -> None:
        meta = pd.DataFrame({column: [] for column in semantic.META_COLUMNS})
        matrix = np.empty((0, semantic.DIMENSIONS), dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(semantic, "CACHE_DIR", Path(directory)), \
                patch.object(semantic, "current_embedding_version", return_value="vector-v11"), \
                patch.object(semantic, "_download_embeddings", return_value=(meta, matrix)):
            semantic.load_embeddings.cache_clear()
            semantic.load_embeddings(refresh=True)
            self.assertTrue((Path(directory) / "embeddings_vector-v11.npz").is_file())
            semantic.load_embeddings.cache_clear()


if __name__ == "__main__":
    unittest.main()
