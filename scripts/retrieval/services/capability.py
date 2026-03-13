from __future__ import annotations

import json

EXIT_UNSUPPORTED = 4


def emit_unsupported(*, corpus: str, operation: str, reason: str) -> int:
    print(
        json.dumps(
            {
                "status": "unsupported_operation",
                "corpus": corpus,
                "operation": operation,
                "reason": reason,
            },
            sort_keys=True,
        )
    )
    return EXIT_UNSUPPORTED
