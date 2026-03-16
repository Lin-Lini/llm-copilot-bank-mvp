import unittest

from libs.common.embeddings import _project_hash


class TestEmbeddings(unittest.TestCase):
    def test_project_hash_dim_and_norm(self):
        v = [0.1] * 1536
        out = _project_hash(v, 64)
        self.assertEqual(len(out), 64)
        # norm ~= 1
        s = sum(x * x for x in out)
        self.assertTrue(0.99 <= s <= 1.01)


if __name__ == '__main__':
    unittest.main()
