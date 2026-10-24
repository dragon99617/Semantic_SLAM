#!/usr/bin/env python3
"""Verify missing-data adaptation preserves dense behavior and never fills holes."""
import sys
from pathlib import Path
import unittest

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'third_party/OVO'))
from ovo.utils.geometry_utils import depth_filter
from sparse_depth_filter import valid_mask_depth_filter, valid_mask_pooling


class SparseFilterTests(unittest.TestCase):
    def test_dense_exact_author_equivalence(self):
        torch.manual_seed(4)
        dense = torch.rand(32, 40)*.2+2
        self.assertTrue(torch.equal(depth_filter(dense), valid_mask_depth_filter(dense)))

    def test_sparse_plane_preserved_without_creating_depth(self):
        sparse = torch.zeros(32, 40)
        sparse[::4, ::4] = 3
        filtered = valid_mask_depth_filter(sparse)
        self.assertTrue(torch.equal(sparse, filtered))
        self.assertEqual(int((depth_filter(sparse) > 0).sum()), 0)

    def test_empty_and_nonfinite(self):
        depth = torch.zeros(32, 40)
        depth[10, 10] = float('nan')
        depth[15, 15] = float('inf')
        self.assertTrue(torch.equal(valid_mask_depth_filter(depth), torch.zeros_like(depth)))

    def test_observed_discontinuity_is_still_rejected(self):
        depth = torch.ones(32, 40)*3
        depth[16, 20] = 4
        self.assertEqual(valid_mask_depth_filter(depth)[16, 20].item(), -1)
        self.assertEqual(valid_mask_depth_filter(depth)[0, 0].item(), 3)

    def test_pooling_dense_author_equivalence(self):
        torch.manual_seed(5)
        candidate = torch.rand(32, 40) > .1
        author = ~torch.nn.functional.max_pool2d((~candidate)[None].float(), 3, 1, 1)[0].bool()
        self.assertTrue(torch.equal(author, valid_mask_pooling(candidate, torch.ones_like(candidate))))

    def test_pooling_preserves_new_sparse_points(self):
        observed = torch.zeros(32, 40, dtype=torch.bool)
        observed[::4, ::4] = True
        self.assertTrue(torch.equal(observed, valid_mask_pooling(observed, observed)))
        candidate = observed.clone()
        candidate[8, 8] = False  # actual matched observation suppresses nearby candidates
        candidate[8, 9] = observed[8, 9] = True
        self.assertFalse(valid_mask_pooling(candidate, observed)[8, 9])
        self.assertTrue(valid_mask_pooling(candidate, observed)[12, 12])


if __name__ == '__main__':
    unittest.main(verbosity=2)
