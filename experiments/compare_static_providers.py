"""Issue #25: offline plan/freeze/score; never calls an external API.

Keep all supplied files and outputs outside the public checkout.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mtg_jev.comparison import SCENES, freeze_inputs, make_plan, score_records


def load(path):
    return json.loads(Path(path).read_text())


def run(args):
    if args.command == 'plan' and Path(args.output).resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Private manifest must stay outside the public checkout')
    if args.command == 'plan':
        result = make_plan(args.repeats)
        paths = (args.scene_a, args.scene_b, args.truth_a, args.truth_b)
        if any(paths):
            if not all(paths):
                raise ValueError('Supply both images and both truth files together')
            result = freeze_inputs(result, dict(zip(SCENES, paths[:2])),
                                   dict(zip(SCENES, (load(p) for p in paths[2:]))))
    else:
        result = score_records(load(args.manifest), load(args.records),
                               dict(zip(SCENES, (load(args.truth_a), load(args.truth_b)))))
    # Exclusive create: never silently overwrite a frozen protocol or score.
    with Path(args.output).open('x') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': result['status'], 'api_calls_this_command': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--repeats', type=int, default=4)
    plan.add_argument('--scene-a')
    plan.add_argument('--scene-b')
    plan.add_argument('--truth-a')
    plan.add_argument('--truth-b')
    score = commands.add_parser('score')
    score.add_argument('--manifest', required=True)
    score.add_argument('--records', required=True)
    score.add_argument('--truth-a', required=True)
    score.add_argument('--truth-b', required=True)
    for command in (plan, score):
        command.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
        parser.exit(2, 'Comparison input invalid, unavailable or output already exists; contents omitted\n')
