"""API-free demos by default; live requires separate explicit approvals."""

import argparse
import json
import sys
from pathlib import Path

from .detector import Settings, detect
from .fixtures import generate
from .geometry import Box, evaluate_documents
from .images import MAX_FILE, crop, overlay, prepare
from .provider import JevClient, LiveApproval, MockClient


def read_json(path):
    if Path(path).stat().st_size > 2 * 1024 * 1024:
        raise ValueError("JSON file exceeds 2 MiB")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                          encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jev MTG localization PoC; mock is not vision")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Generate original rectangles, truth and mock scripts")
    demo.add_argument("--output", default="demo")
    run = sub.add_parser("detect")
    run.add_argument("image")
    run.add_argument("--mode", choices=("mock", "live"), default="mock")
    run.add_argument("--mock-script")
    run.add_argument("--output", required=True, help="New output directory; must not exist")
    run.add_argument("--max-cards", type=int, default=8)
    run.add_argument("--max-requests", type=int, default=10)
    run.add_argument("--approve-live", action="store_true")
    run.add_argument("--budget-usd", type=float, default=0)
    run.add_argument("--save-crops", action="store_true")
    evaluation = sub.add_parser("evaluate")
    evaluation.add_argument("prediction")
    evaluation.add_argument("truth")
    evaluation.add_argument("--iou", type=float, default=0.5)
    evaluation.add_argument("--output")
    serve = sub.add_parser("serve", help="Loopback-only, mock-only Web UI")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            generate(args.output)
            print("Generated synthetic rectangles and scripted answers. Not MTG recognition.")
        elif args.command == "detect":
            settings = Settings(max_cards=args.max_cards, max_requests=args.max_requests)
            if args.mode == "live":
                # This checks the approval before reading any image or key.
                approval = LiveApproval(args.approve_live, args.max_requests, args.budget_usd)
                approval.validate()
            if Path(args.output).exists():
                raise ValueError("Output directory already exists; choose a new directory")
            image_path = Path(args.image)
            if image_path.stat().st_size > MAX_FILE:
                raise ValueError("Image exceeds 16 MiB")
            prepared = prepare(image_path.read_bytes())
            if args.mode == "mock":
                if not args.mock_script:
                    raise ValueError("Mock mode requires --mock-script; boxes are never inferred")
                script = read_json(args.mock_script)
                if script.get("image") != {"width": prepared.original.width,
                                           "height": prepared.original.height}:
                    raise ValueError("Mock script image dimensions differ")
                client = MockClient(script["responses"])
            else:
                client = JevClient(approval)
            result = detect(prepared, client, settings)
            directory = Path(args.output)
            directory.mkdir(parents=True)
            write_json(directory / "result.json", result)
            overlay(prepared, result).save(directory / "overlay.png")
            if args.save_crops:
                for d in result["detections"]:
                    crop(prepared, Box.read(d["box"])).save(directory / f'{d["id"]}.png')
            print(json.dumps({k: result[k] for k in ("mode", "evidence", "status", "stop_reason", "api_calls")}))
            return 2 if result["status"] == "error" else 0
        elif args.command == "evaluate":
            metrics = evaluate_documents(read_json(args.prediction), read_json(args.truth), args.iou)
            if args.output:
                write_json(args.output, metrics)
            print(json.dumps(metrics, indent=2, allow_nan=False))
        elif args.command == "serve":
            from .server import serve
            serve(args.port)
    except (ValueError, OSError, KeyError, TypeError):
        # Do not print arbitrary exception bodies, paths, image bytes, or credentials.
        print("Input/configuration error. Check formats, flags, limits and output directory.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
