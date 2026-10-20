"""Offline evaluation only; never import this file into the inference process."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mtg_jev.geometry import Box, evaluate_documents, iou


def evaluate_layer(predictions, result, truth, threshold):
    document = {**result, 'detections': predictions}
    score = evaluate_documents(document, truth, threshold)
    boxes, targets = [Box.read(p['box']) for p in predictions], [Box.read(b) for b in truth['boxes']]
    eligible = sum(any(iou(b, t) >= threshold for t in targets) for b in boxes)
    parents = list(range(len(boxes)))

    def root(n):
        while parents[n] != n:
            n = parents[n]
        return n

    pairs = 0
    for a in range(len(boxes)):
        for b in range(a+1, len(boxes)):
            if iou(boxes[a], boxes[b]) >= .85:
                pairs += 1
                parents[root(a)] = root(b)
    score.update(proposals=len(boxes), unique_matched_truth=score['tp'],
                 duplicate_truth_predictions=eligible-score['tp'],
                 geometric_unique_groups=len({root(n) for n in range(len(boxes))}),
                 geometric_duplicate_pairs=pairs)
    score['missed_truth_ids'] = [n+1 for n in range(len(targets))
                                 if n not in {m['truth'] for m in score['matches']}]
    return score


def summarize(values):
    values = [v for v in values if v is not None]
    return {'mean': statistics.mean(values), 'min': min(values), 'max': max(values)} if values else None


def run(args):
    folder = Path(args.results)
    summary = json.loads((folder/'summary.json').read_text())  # Exists only after inference ends.
    runs = json.loads((folder/'runs.json').read_text())
    protocol = json.loads((folder/'protocol.json').read_text())
    http = json.loads((folder/'http.json').read_text())
    if len(runs) != summary['frame_runs']:
        raise ValueError('Inference manifest incomplete')
    for r in runs:
        if not (folder/f"r{r['repeat']}-{r['scene']}-{r['condition']}.json").exists():
            raise ValueError('Prediction missing; do not open truth')
    truths = {}
    for scene, filename in [('scene-A', args.truth_a), ('scene-B', args.truth_b)]:
        truth = json.loads(Path(filename).read_text())
        size = protocol['inputs'][scene]
        if truth['image']['width']*size['height'] != truth['image']['height']*size['width']:
            raise ValueError('Truth/source aspect ratio differs')
        # Normalized xyxy is invariant under uniform resizing of the same image.
        truth['image'] = {k: size[k] for k in ('width', 'height')}
        truths[scene] = truth
    evaluated = []
    for r in runs:
        name = f"r{r['repeat']}-{r['scene']}-{r['condition']}"
        result = json.loads((folder/(name+'.json')).read_text())
        invalid = {'search_raw': 0, 'crop_raw': 0}
        for h in http:
            if any(h[k] != r[k] for k in ('repeat', 'scene', 'condition')) or h['status'] != 'response_with_usage':
                continue
            response = json.loads((folder/f"http-{h['attempt']:03d}.json").read_text())
            answer = response.get('answers', {}).get('card', {})
            try:
                if answer.get('type') != 'box':
                    raise ValueError('Missing Box')
                Box.read(answer['box'])
            except (ValueError, TypeError, KeyError):
                invalid['search_raw' if h['questions'] == 2 else 'crop_raw'] += 1
        scores = {}
        for layer, key in [('search_raw', 'raw_search'), ('crop_raw', 'raw_crop'), ('adopted', 'detections')]:
            scores[layer] = {str(t): evaluate_layer(result[key], result, truths[r['scene']], t)
                             for t in (.5, .75)}
        evaluated.append({**r, 'scores': scores, 'invalid_raw_boxes': invalid})
    (folder/'scored-runs.json').write_text(json.dumps(evaluated, indent=2))
    complete = [repeat for repeat in range(1, protocol['repeats']+1)
                if sum(r['repeat'] == repeat for r in runs) == 8]
    groups = defaultdict(list)
    for r in evaluated:
        if r['repeat'] in complete:
            groups[r['scene'], r['condition']].append(r)
    aggregate = []
    for (scene, condition), subset in groups.items():
        row = {'scene': scene, 'condition': condition, 'repeats': len(subset), 'layers': {}}
        for layer in ('search_raw', 'crop_raw', 'adopted'):
            row['layers'][layer] = {}
            for threshold in ('0.5', '0.75'):
                row['layers'][layer][threshold] = {k: summarize([r['scores'][layer][threshold][k] for r in subset])
                    for k in ('tp', 'fp', 'fn', 'proposals', 'mean_matched_iou', 'duplicate_truth_predictions',
                              'geometric_unique_groups', 'geometric_duplicate_pairs')}
        for metric in ('seconds', 'http', 'estimated_cost_usd'):
            row[metric] = summarize([r[metric] for r in subset])
        row['per_repeat_tp50'] = [r['scores']['adopted']['0.5']['tp'] for r in sorted(subset, key=lambda r:r['repeat'])]
        row['per_repeat_tp75'] = [r['scores']['adopted']['0.75']['tp'] for r in sorted(subset, key=lambda r:r['repeat'])]
        row['invalid_raw_boxes'] = {layer: sum(r['invalid_raw_boxes'][layer] for r in subset)
                                    for layer in ('search_raw', 'crop_raw')}
        row['answer_error_frames'] = sum(r['status'] == 'error' for r in subset)
        aggregate.append(row)
    lookup = {(r['scene'], r['repeat'], r['condition']): r for r in evaluated if r['repeat'] in complete}
    effects = []
    for scene in ('scene-A', 'scene-B'):
        for factor in ('generic-minus-mtg', 'explicit-minus-plain'):
            pairs = [('generic_plain', 'mtg_plain'), ('generic_explicit', 'mtg_explicit')] if factor.startswith('generic') else [('mtg_explicit', 'mtg_plain'), ('generic_explicit', 'generic_plain')]
            differences = [lookup[scene, repeat, a]['scores']['adopted']['0.5']['tp']-
                           lookup[scene, repeat, b]['scores']['adopted']['0.5']['tp']
                           for repeat in complete for a, b in pairs]
            effects.append({'scene': scene, 'factor': factor, 'paired_tp50_difference': summarize(differences),
                'wins': sum(d>0 for d in differences), 'ties': sum(d==0 for d in differences),
                'losses': sum(d<0 for d in differences), 'differences': differences})
    report = {'complete_repeats': complete, 'incomplete_runs': len(runs)-8*len(complete),
              'aggregate': aggregate, 'effects': effects, 'phase': summary}
    (folder/'aggregate.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'complete_repeats': complete, 'incomplete_runs': report['incomplete_runs'],
                      'groups': len(aggregate), 'http': summary['http']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', required=True)
    parser.add_argument('--truth-a', required=True)
    parser.add_argument('--truth-b', required=True)
    run(parser.parse_args())
