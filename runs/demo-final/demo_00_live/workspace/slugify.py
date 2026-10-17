import re

def slugify(text: str) -> str:
    """Return a slugified version of *text*.

    The function lowercases the input, removes all characters that are not
    letters, numbers, or whitespace (including Unicode letters and digits),
    collapses any sequence of whitespace into a single hyphen, and returns the
    resulting string. An empty input yields an empty string.
    """
    if not text:
        return ""
    # Preserve letters and digits (including Unicode) and whitespace; drop everything else.
    # Using a comprehension with str.isalnum() and str.isspace() respects Unicode.
    cleaned = ''.join(ch for ch in text if ch.isalnum() or ch.isspace())
    # Lowercase the cleaned string.
    cleaned = cleaned.lower()
    # Split on any whitespace (this collapses multiple whitespace) and join with hyphens.
    parts = cleaned.split()
    return '-'.join(parts)
