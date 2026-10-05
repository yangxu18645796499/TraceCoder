"""WSL-only entry point. No legacy dataset/evaluator/API modules are imported."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import platform
import sys
from .aligned_client import GoClient
from .aligned_pipeline import Pipeline
from .aligned_protocol import load_public, sha256


def main():
    parser = argparse.ArgumentParser(description='Aligned public-only TraceCoder; frozen scope required')
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--experiment-dir', type=Path, required=True)
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--no-two-step-repair', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or 'microsoft' not in platform.release().lower() or platform.python_version() != '3.12.3':
        raise SystemExit('Locked WSL/Linux CPython 3.12.3 is required')
    root = args.data_root.resolve()
    sys.path.insert(0, str(root / 'scripts'))
    from tracecoder_experiment import verify_freeze
    from aligned_debug import evaluate_debug, feedback
    freeze = verify_freeze(args.freeze, root, Path(__file__).resolve().parents[1])
    if args.no_two_step_repair != (not freeze['two_step']):
        raise SystemExit('Two-step CLI setting does not match freeze')
    if str(args.experiment_dir.resolve()) != freeze['experiment_dir']:
        raise SystemExit('Experiment directory does not match freeze')
    pipeline = Pipeline(args.experiment_dir, client=GoClient(args.experiment_dir / 'unused',
                        max_tokens=freeze['request']['max_tokens'], timeout=freeze['request']['timeout']),
                        debug=evaluate_debug, format_feedback=feedback, limits=freeze['limits'],
                        two_step=freeze['two_step'], freeze_sha256=sha256(args.freeze.read_bytes()))
    for dataset, ids in freeze['scope'].items():
        if dataset == 'humaneval_plus':
            raise SystemExit('HumanEval+ must reuse base HumanEval generation')
        questions = {p['problem_id']: p for p in load_public(root, dataset)}
        for identity in ids:
            record = pipeline.run(questions[identity], freeze['methods'])
            print(json.dumps({'dataset': dataset, 'problem_id': identity, 'status': record['status'],
                              'stop_reasons': {m: t['stop_reason'] for m,t in record['methods'].items()} }), flush=True)


if __name__ == '__main__':
    main()
