"""Issue #25: private preflight and explicitly enabled Decisions predicates.

No live request is made by default. Original approved files must be supplied;
this program never searches private storage or retrieves any external artifact.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mtg_jev.decisions_budget import BudgetError, ComparisonBudget
from mtg_jev.decisions_transport import DecisionsClient, DecisionsError, FrozenVerification


def run(args):
    protocol = FrozenVerification(args.manifest)
    # Initialization is offline and may preserve a historical price snapshot.
    # Both preflight and run require that snapshot to be current before use.
    preflight = protocol.preflight(require_current=args.command != 'init-budget')
    with ComparisonBudget(args.ledger, protocol.sha256, protocol.questions,
                          args.prior_spend_usd, create=args.command == 'init-budget') as budget:
        summary = {**preflight, **budget.preflight()}
        if args.command == 'run':
            result = DecisionsClient(protocol, budget, approve_live=args.approve_live).run()
            # Predicates and usage are already durably stored in the private
            # ledger. Console output is fixed labels and request counts only.
            summary = {'status': 'complete', 'evidence': result['evidence'],
                       'api_calls_this_command': result['api_calls_this_command'],
                       'completed_requests': len(result['results'])}
        print(json.dumps(summary, allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init-budget', 'preflight', 'run'))
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--prior-spend-usd', required=True,
                        help='Known cumulative prior spend for this comparison, including other providers')
    parser.add_argument('--approve-live', action='store_true')
    args = parser.parse_args()
    try:
        run(args)
    except (DecisionsError, BudgetError, OSError, ValueError, TypeError, KeyboardInterrupt):
        parser.exit(2, 'Decisions stopped; inputs/accounting unavailable or live disabled; inspect private ledger; no retry\n')
