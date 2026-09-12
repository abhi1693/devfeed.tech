"""Remove feed presentation markers without changing technical names."""

import re
import unicodedata

QUOTE_PAIRS = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’"}


def normalize_tag_name(value: str) -> str:
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()
    while name:
        previous = name
        if len(name) > 1 and QUOTE_PAIRS.get(name[0]) == name[-1]:
            name = name[1:-1].strip()
        name = name.lstrip("#").strip()
        if name == previous:
            break
    return name
