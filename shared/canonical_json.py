"""Serialização JSON canônica (chaves ordenadas, sem espaços).

Usada onde o mesmo conteúdo precisa ser byte-a-byte idêntico entre gravações e
republicações, por exemplo nos itens do pedido (D-02).
"""

import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
