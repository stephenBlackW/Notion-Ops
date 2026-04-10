"""Notion ID extraction utilities."""


def extract_notion_id(id_or_url: str) -> str:
    """Extract a Notion ID from a URL or return a bare ID as-is.

    Handles URLs like:
        https://www.notion.so/workspace/Page-Title-<id>
        https://www.notion.so/<id>
        https://www.notion.so/workspace/Page-Title-<id>#<block_id>

    Args:
        id_or_url: A Notion page/block/database URL or bare ID string.

    Returns:
        The 32-character hex ID (dashes removed).
    """
    if id_or_url.startswith("http"):
        # Handle block IDs after # fragment
        if "#" in id_or_url:
            fragment = id_or_url.split("#")[-1]
            if len(fragment) == 32:
                return fragment

        # Extract ID from Notion URL
        # URL format: https://www.notion.so/workspace/Page-Title-<id>
        # or https://www.notion.so/<id>
        parts = id_or_url.rstrip("/").split("-")
        if parts:
            potential_id = parts[-1]
            # Notion IDs are 32 hex characters (without dashes)
            if len(potential_id) == 32:
                return potential_id
        # Try extracting from path
        path = id_or_url.split("notion.so/")[-1].split("?")[0]
        if "#" in path:
            path = path.split("#")[-1]
        if "/" in path:
            path = path.split("/")[-1]
        # Remove any title prefix
        if "-" in path:
            path = path.split("-")[-1]
        return path

    # Remove dashes if present (normalize ID format)
    return id_or_url.replace("-", "")
