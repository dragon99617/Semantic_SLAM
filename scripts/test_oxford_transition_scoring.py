#!/usr/bin/env python3
"""Independent hand-calculated checks of abstention-aware semantic metrics."""
import unittest
import csv
import hashlib
import json
from pathlib import Path
import tempfile

import cv2
import numpy as np

from score_oxford_transition_annotations import confusion_matrix, metrics, score


class ScoringTests(unittest.TestCase):
    def test_abstentions_count_as_false_negatives(self):
        truth = np.array([[0, 0, 1, 1, 255]], np.uint8)
        pred = np.array([[0, 255, 1, 0, 1]], np.uint8)
        matrix = confusion_matrix(truth, pred, 2)
        np.testing.assert_array_equal(matrix, [[1, 0, 1], [1, 1, 0]])
        value = metrics(matrix)
        self.assertEqual(value['pixel_accuracy'], .5)
        self.assertEqual(value['prediction_coverage'], .75)
        self.assertAlmostEqual(value['conditional_pixel_accuracy'], 2/3)
        self.assertAlmostEqual(value['mean_iou'], (1/3+1/2)/2)

    def test_no_predictions_is_zero_score_with_coverage_zero(self):
        matrix = confusion_matrix(np.array([0, 1]), np.array([255, 255]), 2)
        value = metrics(matrix)
        self.assertEqual(value['pixel_accuracy'], 0)
        self.assertEqual(value['mean_iou'], 0)
        self.assertEqual(value['prediction_coverage'], 0)
        self.assertIsNone(value['conditional_pixel_accuracy'])

    def test_no_labelled_pixels_has_no_numeric_accuracy(self):
        value = metrics(confusion_matrix(np.array([255]), np.array([0]), 2))
        self.assertIsNone(value['pixel_accuracy'])
        self.assertIsNone(value['mean_iou'])

    def test_invalid_class_is_rejected(self):
        with self.assertRaises(ValueError):
            confusion_matrix(np.array([0]), np.array([3]), 2)

    def make_package(self, root, status):
        for name in ('images', 'valid_masks', 'labels', 'predictions'):
            (root/name).mkdir()
        image, valid = root/'images/s.png', root/'valid_masks/s.png'
        cv2.imwrite(str(image), np.zeros((2, 2, 3), np.uint8))
        cv2.imwrite(str(valid), np.full((2, 2), 255, np.uint8))
        ontology = root/'ontology.json'
        ontology.write_text(json.dumps(dict(classes=[dict(name='zero'), dict(name='one')])))
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        manifest = dict(ontology_sha256=digest(ontology), samples=[dict(sample_id='s',
            image_path='images/s.png', image_sha256=digest(image), valid_mask_path='valid_masks/s.png', valid_mask_sha256=digest(valid),
            label_path='labels/s.png', height=2, width=2,
            memberships=[dict(event_id='event1', window='before'), dict(event_id='event2', window='after')])])
        (root/'manifest.json').write_text(json.dumps(manifest))
        with (root/'tasks.csv').open('w') as f:
            writer = csv.DictWriter(f, fieldnames=['sample_id', 'annotation_status', 'annotator', 'reviewer'])
            writer.writeheader()
            writer.writerow(dict(sample_id='s', annotation_status=status, annotator='person_a', reviewer='person_b'))
        (root/'predictions/index.json').write_text(json.dumps([dict(sample_id='s', map_image_stamp_ns=10, raw_image_stamp_ns=11)]))
        return root/'predictions'

    def test_pending_annotations_cannot_produce_accuracy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prediction = self.make_package(root, 'pending')
            result = score(root, [prediction])
            self.assertEqual(result['status'], 'awaiting_independent_annotation')
            self.assertEqual(result['metrics'], {})
            self.assertFalse(result['semantic_accuracy_accepted'])

    def test_reviewed_scores_deduplicate_global_images(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prediction = self.make_package(root, 'reviewed')
            cv2.imwrite(str(root/'labels/s.png'), np.array([[0, 0], [1, 255]], np.uint8))
            cv2.imwrite(str(prediction/'s.png'), np.array([[0, 255], [1, 0]], np.uint8))
            np.savez_compressed(prediction/'s_instances.npz', geometry_support=np.array([[1, 0], [1, 1]], bool))
            result = score(root, [prediction])
            global_score = result['metrics']['global_unique_images']
            self.assertEqual(global_score['labelled_pixels'], 3)
            self.assertAlmostEqual(global_score['pixel_accuracy'], 2/3)
            self.assertEqual(result['metrics']['event1/before']['labelled_pixels'], 3)
            self.assertEqual(result['metrics']['event2/after']['labelled_pixels'], 3)
            self.assertEqual(result['metrics_on_projected_geometry']['global_unique_images']['pixel_accuracy'], 1)

    def test_reviewed_empty_template_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prediction = self.make_package(root, 'reviewed')
            cv2.imwrite(str(root/'labels/s.png'), np.full((2, 2), 255, np.uint8))
            with self.assertRaises(ValueError):
                score(root, [prediction])

    def test_future_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prediction = self.make_package(root, 'pending')
            (prediction/'index.json').write_text(json.dumps([dict(sample_id='s', map_image_stamp_ns=12, raw_image_stamp_ns=11)]))
            with self.assertRaises(ValueError):
                score(root, [prediction])


if __name__ == '__main__':
    unittest.main(verbosity=2)
