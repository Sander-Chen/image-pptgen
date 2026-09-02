"""Markdown auto-splitting with LLM fallback for deck content.

Splits a deck's full markdown content into individual slide chunks
suitable for the HTML generation pipeline.
"""

import json
import logging
import re

log = logging.getLogger("ppt-splitter")


def _has_substantive_opening(lines: list[str]) -> bool:
    thematic_break = re.compile(r"^\s*([*_\-])(?:\s*\1){2,}\s*$")
    return any(line.strip() and not thematic_break.fullmatch(line) for line in lines)


def split_by_markdown(content: str) -> list[dict] | None:
    """Try to split content by markdown headings. H3 first, then H2.

    Returns a list of {title, content, split_mode} dicts, or None if no
    headings of the chosen level are found.

    Strategy:
    - If H3 headings (###) are present, split on those.
    - Otherwise, if H2 headings (##) are present, split on those.
    - If neither is found, return None to signal the caller to use LLM.
    """
    # Try H3 first
    result = _split_at_level(content, level=3)
    if result:
        log.info("Split by H3 headings: %d slides", len(result))
        return result

    # Fall back to H2
    result = _split_at_level(content, level=2)
    if result:
        log.info("Split by H2 headings: %d slides", len(result))
        return result

    log.info("No H2/H3 headings found in content")
    return None


def split_by_explicit_h1(content: str) -> list[dict] | None:
    """Split on explicit ATX H1 page sections when two or more exist.

    Reuses the fence- and comment-safe `_split_at_level` scanner. Documents
    with fewer than two explicit H1 headings return None so callers can fall
    back to the global H3-then-H2 parser.
    """
    result = _split_at_level(content, level=1)
    if result:
        log.info("Split by explicit H1 headings: %d slides", len(result))
        return result
    return None


def _split_at_level(content: str, level: int) -> list[dict] | None:
    """Split content at a specific heading level.

    Returns list of {title, content, split_mode} or None if fewer than 2
    sections would result (meaning the heading level is not useful for splitting).
    """
    prefix = "#" * level
    # Pattern matches lines starting with exactly `level` # characters
    # followed by a space and the heading text.
    # We use a negative lookbehind/lookahead to avoid matching higher-level headings.
    pattern = rf"^{prefix}\s+(.+)$"

    lines = content.split("\n")
    sections: list[dict] = []
    current_title = None
    current_lines: list[str] = []
    preamble_lines: list[str] = []
    parent_pattern = r"^##\s+(.+)$" if level == 3 else None
    document_pattern = r"^#\s+(.+)$" if level == 3 else None
    leading_document_title: str | None = None
    pending_parent_lines: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    in_html_comment = False

    for line in lines:
        heading_protected = fence_char is not None or in_html_comment
        if fence_char is not None:
            if re.match(
                rf"^\s{{0,3}}{re.escape(fence_char)}{{{fence_length},}}\s*$",
                line,
            ):
                fence_char = None
                fence_length = 0
        else:
            fence_match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if fence_match:
                heading_protected = True
                fence_char = fence_match.group(1)[0]
                fence_length = len(fence_match.group(1))
            elif in_html_comment:
                if "-->" in line:
                    in_html_comment = False
            elif (comment_start := line.find("<!--")) >= 0:
                if not line[:comment_start].strip():
                    heading_protected = True
                if "-->" not in line[comment_start + 4:]:
                    in_html_comment = True

        match = None if heading_protected else re.match(pattern, line)
        if match:
            # Save previous section
            if current_title is not None:
                sections.append({
                    "title": current_title,
                    "content": "\n".join(current_lines).strip(),
                    "split_mode": f"h{level}",
                })
            elif current_lines:
                # Content before the first heading — save as preamble
                preamble_lines = current_lines[:]

            child_title = match.group(1).strip()
            parent_match = (
                re.match(parent_pattern, pending_parent_lines[0])
                if parent_pattern and pending_parent_lines
                else None
            )
            parent_title = parent_match.group(1).strip() if parent_match else None
            child_matches_parent = bool(
                parent_title
                and " ".join(parent_title.split()) == " ".join(child_title.split())
            )

            if (
                leading_document_title is not None
                and not sections
                and parent_title is not None
                and _has_substantive_opening(preamble_lines)
            ):
                sections.append({
                    "title": leading_document_title,
                    "content": "\n".join(preamble_lines).strip(),
                    "split_mode": f"h{level}",
                })
                preamble_lines = []
                current_title = parent_title
                current_lines = pending_parent_lines[1:]
                if not child_matches_parent:
                    current_lines.append(line)
            elif leading_document_title is not None and not sections:
                current_title = leading_document_title
                current_lines = pending_parent_lines[:]
                if not child_matches_parent:
                    current_lines.append(line)
            elif parent_title is not None:
                current_title = parent_title
                current_lines = pending_parent_lines[1:]
                if not child_matches_parent:
                    current_lines.append(line)
            else:
                current_title = child_title
                current_lines = []
            pending_parent_lines = []
        elif (
            document_pattern
            and not heading_protected
            and current_title is None
            and leading_document_title is None
            and (document_match := re.match(document_pattern, line))
        ):
            leading_document_title = document_match.group(1).strip()
        elif parent_pattern and not heading_protected and re.match(parent_pattern, line):
            pending_parent_lines.append(line)
        elif pending_parent_lines:
            pending_parent_lines.append(line)
        else:
            current_lines.append(line)

    # Save the last section
    if current_title is not None:
        current_lines.extend(pending_parent_lines)
        sections.append({
            "title": current_title,
            "content": "\n".join(current_lines).strip(),
            "split_mode": f"h{level}",
        })

    if len(sections) < 2:
        return None

    # If there was a preamble before the first heading, prepend it to the
    # first section's content (preserving context).
    if preamble_lines and sections:
        preamble_text = "\n".join(preamble_lines).strip()
        if preamble_text:
            sections[0]["content"] = preamble_text + "\n\n" + sections[0]["content"]

    return sections


def split_by_llm(content: str, llm_config: dict) -> list[dict]:
    """Call an LLM to split content into slide-sized chunks.

    The LLM is asked to return a JSON array of {title, content} objects.
    Each result is tagged with split_mode='llm'.

    Args:
        content: The full deck markdown content.
        llm_config: Dict with api_type, endpoint, model, api_key, etc.

    Returns:
        List of {title, content, split_mode} dicts.

    Raises:
        ValueError: If the LLM response cannot be parsed.
        requests.HTTPError: If the API call fails.
    """
    from pipeline import call_llm, extract_fenced_block

    prompt = f"""You are a presentation content analyst. Your task is to split the following deck content into individual slides.

Each slide should be a self-contained section suitable for a single presentation slide. Aim for 3-8 slides total, depending on content length and natural topic boundaries.

Rules:
- Each slide should have a clear, concise title (under 60 characters).
- Each slide's content should be a coherent chunk of the original text.
- Preserve ALL original content — do not summarize or remove text.
- Split at natural topic boundaries.
- Maintain the original order of content.

Return ONLY a JSON array of objects, each with "title" and "content" fields:

```json
[
  {{"title": "Slide Title 1", "content": "Full content for slide 1..."}},
  {{"title": "Slide Title 2", "content": "Full content for slide 2..."}},
  ...
]
```

Content to split:
---
{content}
---

Return the JSON array now:"""

    raw_response = call_llm(llm_config, prompt)

    # Extract JSON from the response
    json_text = extract_fenced_block(raw_response, "json")
    try:
        slides_data = json.loads(json_text)
    except json.JSONDecodeError as e:
        log.error("Failed to parse LLM split response: %s", e)
        raise ValueError(f"LLM returned invalid JSON for slide splitting: {e}") from e

    if not isinstance(slides_data, list) or len(slides_data) == 0:
        raise ValueError("LLM returned empty or non-list result for slide splitting")

    # Validate and normalize each entry
    results = []
    for i, item in enumerate(slides_data):
        if not isinstance(item, dict):
            raise ValueError(f"Slide {i} is not a dict: {type(item)}")
        title = item.get("title", f"Slide {i + 1}").strip()
        slide_content = item.get("content", "").strip()
        if not slide_content:
            log.warning("Slide %d (%s) has empty content, skipping", i, title)
            continue
        results.append({
            "title": title,
            "content": slide_content,
            "split_mode": "llm",
        })

    if not results:
        raise ValueError("LLM splitting produced no valid slides")

    log.info("LLM split produced %d slides", len(results))
    return results


def split_deck(content: str, llm_config: dict | None = None) -> list[dict]:
    """Main entry point: try markdown splitting first, fall back to LLM.

    Args:
        content: The full deck markdown content.
        llm_config: Optional LLM config dict for fallback splitting.
            Required if content has no markdown headings.

    Returns:
        List of {title, content, split_mode} dicts.

    Raises:
        ValueError: If no headings are found and no llm_config is provided.
    """
    # Try markdown-based splitting first
    result = split_by_markdown(content)
    if result is not None:
        return result

    # No headings found — need LLM
    if llm_config is None:
        raise ValueError(
            "Content has no H2/H3 headings for automatic splitting, "
            "and no llm_config was provided for LLM-based splitting."
        )

    log.info("No markdown headings found, falling back to LLM splitting")
    return split_by_llm(content, llm_config)
