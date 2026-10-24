#!/usr/bin/env python3
"""Score independently reviewed PNG labels; never score empty label templates.

Unknown predictions count as false negatives in full-support IoU/accuracy.
Conditional accuracy and prediction coverage are reported separately. Global
scores deduplicate images; per-event/window scores retain declared overlap.
"""
import argparse
import collections
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from verify_phase1_handoff import sha


def confusion_matrix(truth, prediction, classes):
    truth, prediction = np.asarray(truth), np.asarray(prediction)
    if truth.shape != prediction.shape:
        raise ValueError('Prediction/label shape mismatch')
    if not np.isin(truth, [*range(classes), 255]).all() or not np.isin(prediction, [*range(classes), 255]).all():
        raise ValueError('Unknown class ID')
    valid = truth != 255
    predicted = np.where(prediction[valid] == 255, classes, prediction[valid])
    return np.bincount(truth[valid].astype(np.int64)*(classes+1)+predicted,
                       minlength=classes*(classes+1)).reshape(classes, classes+1)


def metrics(matrix):
    n = matrix.shape[0]
    tp = np.diag(matrix[:, :n])
    truth_count, pred_count = matrix.sum(1), matrix[:, :n].sum(0)
    union = truth_count+pred_count-tp
    iou = np.divide(tp, union, out=np.full(n, np.nan), where=union > 0)
    total, known = int(matrix.sum()), int(matrix[:, :n].sum())
    return dict(labelled_pixels=total, predicted_labelled_pixels=known,
                pixel_accuracy=float(tp.sum()/total) if total else None,
                prediction_coverage=float(known/total) if total else None,
                conditional_pixel_accuracy=float(tp.sum()/known) if known else None,
                mean_iou=float(np.nanmean(iou)) if np.isfinite(iou).any() else None,
                per_class_iou=[float(x) if np.isfinite(x) else None for x in iou],
                confusion=matrix.tolist())


def score(package, prediction_dirs):
    manifest = json.loads((package/'manifest.json').read_text())
    ontology = json.loads((package/'ontology.json').read_text())
    if sha(package/'ontology.json') != manifest['ontology_sha256']:
        raise ValueError('Ontology changed after sampling')
    n = len(ontology['classes'])
    tasks = list(csv.DictReader((package/'tasks.csv').open()))
    by_id = {t['sample_id']: t for t in tasks}
    if len(by_id) != len(tasks) or set(by_id) != {s['sample_id'] for s in manifest['samples']}:
        raise ValueError('Task list differs from frozen sampled images')
    sources = {}
    for folder in prediction_dirs:
        for row in json.loads((folder/'index.json').read_text()):
            if row['sample_id'] in sources:
                raise ValueError('Duplicate prediction source for sampled image')
            if row['map_image_stamp_ns'] is not None and row['map_image_stamp_ns'] > row['raw_image_stamp_ns']:
                raise ValueError('Future observations in causal prediction')
            sources[row['sample_id']] = folder
    matrices = collections.defaultdict(lambda: np.zeros((n, n+1), np.int64))
    geometry_matrices = collections.defaultdict(lambda: np.zeros((n, n+1), np.int64))
    scored, pending, unscorable = [], [], []
    notes = json.loads((package/'notes.json').read_text()) if (package/'notes.json').exists() else {}
    for sample in manifest['samples']:
        sid = sample['sample_id']
        task = by_id[sid]
        if sha(package/sample['image_path']) != sample['image_sha256']:
            raise ValueError(f'Blinded annotation image changed: {sid}')
        if sha(package/sample['valid_mask_path']) != sample['valid_mask_sha256']:
            raise ValueError(f'Frozen valid image region changed: {sid}')
        if task['annotation_status'] in ('reviewed', 'unscorable'):
            annotator, reviewer = task['annotator'].strip(), task['reviewer'].strip()
            if not annotator or not reviewer or annotator == reviewer:
                raise ValueError(f'Independent annotation/review provenance missing: {sid}')
        if task['annotation_status'] == 'unscorable':
            if not notes.get(sid):
                raise ValueError(f'Unscorable image needs an explicit reason: {sid}')
            unscorable.append(dict(sample_id=sid, reason=notes[sid]))
            continue
        if task['annotation_status'] != 'reviewed':
            pending.append(sid)
            continue
        label_path = package/sample['label_path']
        truth = cv2.imread(str(label_path), cv2.IMREAD_UNCHANGED)
        if truth is None or truth.dtype != np.uint8 or truth.shape != (sample['height'], sample['width']):
            raise ValueError(f'Label must be original-size single-channel uint8 PNG: {sid}')
        valid_image = cv2.imread(str(package/sample['valid_mask_path']), cv2.IMREAD_GRAYSCALE) > 0
        if (truth[~valid_image] != 255).any() or not (truth != 255).any():
            raise ValueError(f'Invalid-image pixels must be 255; a reviewed image needs labels: {sid}')
        prediction_path = sources.get(sid, Path('/nonexistent'))/(sid+'.png')
        if prediction_path.exists():
            prediction = cv2.imread(str(prediction_path), cv2.IMREAD_UNCHANGED)
            if prediction is None or prediction.dtype != np.uint8 or prediction.shape != truth.shape:
                raise ValueError(f'Invalid prediction PNG: {sid}')
        else:
            prediction = np.full(truth.shape, 255, np.uint8)
        matrix = confusion_matrix(truth, prediction, n)
        support_path = prediction_path.with_name(sid+'_instances.npz')
        support = np.zeros(truth.shape, bool)
        if support_path.exists():
            with np.load(support_path) as data:
                support = data['geometry_support']
            if support.shape != truth.shape or support.dtype != bool:
                raise ValueError(f'Invalid geometry support mask: {sid}')
            if ((prediction != 255) & ~support).any():
                raise ValueError(f'Prediction outside exported map support: {sid}')
        geometry_truth = np.where(support, truth, 255).astype(np.uint8)
        geometry_matrix = confusion_matrix(geometry_truth, prediction, n)
        matrices['global_unique_images'] += matrix
        geometry_matrices['global_unique_images'] += geometry_matrix
        for key in {f"{m['event_id']}/{m['window']}" for m in sample['memberships']}:
            matrices[key] += matrix
            geometry_matrices[key] += geometry_matrix
        scored.append(dict(sample_id=sid, annotator=annotator, reviewer=reviewer,
                           label_sha256=sha(label_path), prediction_sha256=sha(prediction_path) if prediction_path.exists() else None))
    return dict(status=('awaiting_independent_annotation' if not scored else
                       'partial_annotations_scored' if pending else 'independent_annotations_measured'),
                semantic_accuracy_accepted=False, acceptance_reason='No accuracy pass threshold was agreed; report measurements only after independent labels',
                sampled_images=len(manifest['samples']), scored_images=len(scored), pending_images=len(pending),
                unscorable_images=unscorable, pending_sample_ids=pending, reviewed_label_provenance=scored,
                ontology_sha256=manifest['ontology_sha256'], classes=[c['name'] for c in ontology['classes']],
                metrics={k: metrics(v) for k, v in matrices.items()},
                metrics_on_projected_geometry={k: metrics(v) for k, v in geometry_matrices.items()},
                protocol='Sparse causal map rendering. Unknown predictions count as errors on labelled support. Global images deduplicated; overlapping event windows not independent.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('package', type=Path)
    ap.add_argument('--predictions', type=Path, nargs='+', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--require-complete', action='store_true')
    a = ap.parse_args()
    result = score(a.package, a.predictions)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('pending_sample_ids', 'reviewed_label_provenance', 'metrics')}, ensure_ascii=False, indent=2))
    if a.require_complete and (result['pending_images'] or not result['scored_images']):
        raise SystemExit(2)
