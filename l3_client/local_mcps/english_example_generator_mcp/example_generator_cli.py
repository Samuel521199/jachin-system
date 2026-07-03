from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from example_generator import english_generate_example_card, english_example_model_status


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status")
    status.set_defaults(fn=lambda _args: english_example_model_status())

    gen = sub.add_parser("generate")
    gen.add_argument("--word", required=True)
    gen.add_argument("--book-id", default="daily_life_ngsl")
    gen.add_argument("--meaning-cn", default="")
    gen.set_defaults(
        fn=lambda args: english_generate_example_card(
            word=args.word,
            book_id=args.book_id,
            meaning_cn=args.meaning_cn,
        )
    )

    args = parser.parse_args()
    print(json.dumps(args.fn(args), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
