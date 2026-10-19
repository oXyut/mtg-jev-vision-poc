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


def read_json(path, limit=2 * 1024 * 1024):
    if Path(path).stat().st_size > limit:
        raise ValueError("JSON file exceeds size limit")
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
    ocr_demo = sub.add_parser("identification-demo", help="Generate fictional text for local OCR")
    ocr_demo.add_argument("--output", default="demo/identification")
    identify = sub.add_parser("identify", help="Offline crop → OCR → catalog candidates/unknown")
    identify.add_argument("image")
    identify.add_argument("--detections", required=True)
    identify.add_argument("--catalog", required=True)
    identify.add_argument("--output", required=True)
    identify.add_argument("--lang", default="eng")
    identify.add_argument("--top-k", type=int, default=5)
    identify.add_argument("--crop-source", choices=("predicted", "ground_truth"), default="predicted")
    id_eval = sub.add_parser("evaluate-identification")
    id_eval.add_argument("prediction")
    id_eval.add_argument("truth")
    id_eval.add_argument("--iou", type=float, default=.5)
    id_eval.add_argument("--top-k", type=int, default=5)
    id_eval.add_argument("--output")
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
        elif args.command == "identification-demo":
            from .identification_demo import generate_identification_demo
            generate_identification_demo(args.output)
            print("Generated fictional title images and known boxes; not an MTG benchmark.")
        elif args.command == "identify":
            from .identification import Catalog, TesseractOCR, identify_document
            if Path(args.output).exists() or Path(args.image).stat().st_size > MAX_FILE:
                raise ValueError("Choose a new output file and an image within size limits")
            catalog = Catalog(read_json(args.catalog, limit=32 * 1024 * 1024))
            prepared = prepare(Path(args.image).read_bytes())
            result = identify_document(prepared, read_json(args.detections), catalog,
                                       TesseractOCR(args.lang), args.crop_source, args.top_k)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, result)
            print(json.dumps({'method': 'local_ocr', 'external_api_calls': 0,
                              'identified': sum(d['identification']['status'] == 'identified'
                                                for d in result['detections']),
                              'total': len(result['detections'])}))
        elif args.command == "evaluate-identification":
            from .identification import evaluate_identifications
            metrics = evaluate_identifications(read_json(args.prediction), read_json(args.truth),
                                               args.iou, args.top_k)
            if args.output:
                write_json(args.output, metrics)
            print(json.dumps(metrics, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError):
        # Do not print arbitrary exception bodies, paths, image bytes, or credentials.
        print("Input/configuration error. Check formats, flags, limits and output directory.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
