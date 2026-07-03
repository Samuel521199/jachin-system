from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_translate import (
    local_translate_batch_texts,
    local_translate_model_status,
    local_translate_text,
    local_translate_warmup,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status")
    status.set_defaults(fn=lambda _args: local_translate_model_status())

    warmup = sub.add_parser("warmup")
    warmup.add_argument("--direction", default="all")
    warmup.set_defaults(fn=lambda args: local_translate_warmup(direction=args.direction))

    translate = sub.add_parser("translate")
    translate.add_argument("--text", required=True)
    translate.add_argument("--direction", default="auto")
    translate.set_defaults(fn=lambda args: local_translate_text(text=args.text, direction=args.direction))

    translate_batch = sub.add_parser("translate-batch")
    translate_batch.add_argument("--texts-json", required=True, help='JSON array, e.g. ["hello","world"]')
    translate_batch.add_argument("--direction", default="auto")
    translate_batch.set_defaults(
        fn=lambda args: local_translate_batch_texts(
            texts=json.loads(args.texts_json),
            direction=args.direction,
        )
    )

    args = parser.parse_args()
    print(json.dumps(args.fn(args), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
