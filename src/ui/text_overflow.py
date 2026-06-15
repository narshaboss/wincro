"""Small helpers for keeping long dynamic text from breaking CTk layouts."""


def truncate_ui_text(text: object, max_chars: int = 80) -> str:
    """Return a single-line display string capped for compact UI rows."""
    value = str(text or "").replace("\n", " ").strip()
    if len(value) <= max_chars:
        return value
    return value[: max(1, max_chars - 3)].rstrip() + "..."
