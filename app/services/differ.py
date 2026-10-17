"""Text diff generation, side-by-side comparison, and deterministic change summarization."""

from dataclasses import dataclass
from difflib import SequenceMatcher, unified_diff

from app.schemas import DiffSideRow, DiffStats, UnifiedDiffLine


@dataclass
class ComputedDiff:
    """Computed diff artifacts between two text snapshots."""

    stats: DiffStats
    unified_lines: list[UnifiedDiffLine]
    side_by_side_rows: list[DiffSideRow]
    added_text_blob: str


def _truncate_snippet(text: str, max_len: int = 80) -> str:
    """Clean and truncate a line snippet for human-readable one-line summaries."""
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


def generate_change_summary(
    old_lines: list[str],
    new_lines: list[str],
    added_lines: list[str],
    removed_lines: list[str],
) -> str:
    """Produce a concise, deterministic one-line summary of the detected change."""
    if not added_lines and not removed_lines:
        return "No meaningful changes detected between these versions."

    added_count = len(added_lines)
    removed_count = len(removed_lines)

    first_added = _truncate_snippet(added_lines[0], 65) if added_lines else ""
    first_removed = _truncate_snippet(removed_lines[0], 65) if removed_lines else ""

    if added_count > 0 and removed_count == 0:
        suffix = f" (+{added_count - 1} more)" if added_count > 1 else ""
        return f'Added {added_count} line(s): "{first_added}"{suffix}'

    if removed_count > 0 and added_count == 0:
        suffix = f" (-{removed_count - 1} more)" if removed_count > 1 else ""
        return f'Removed {removed_count} line(s): "{first_removed}"{suffix}'

    if added_count == 1 and removed_count == 1:
        return f'Updated "{first_removed}" → "{first_added}"'

    return (
        f'Modified content (+{added_count} / -{removed_count} lines); '
        f'now includes "{first_added}"'
    )


def compute_diff(old_text: str, new_text: str) -> ComputedDiff:
    """Compute unified diff, side-by-side comparison rows, and summary statistics."""
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []

    if old_text == new_text:
        side_rows = [
            DiffSideRow(
                left_line_no=idx + 1,
                left_text=line,
                left_type="equal",
                right_line_no=idx + 1,
                right_text=line,
                right_type="equal",
            )
            for idx, line in enumerate(new_lines)
        ]
        stats = DiffStats(
            added_lines=0,
            removed_lines=0,
            unchanged_lines=len(new_lines),
            similarity_ratio=1.0,
            has_meaningful_changes=False,
            summary="No meaningful changes detected between these versions.",
        )
        return ComputedDiff(
            stats=stats,
            unified_lines=[],
            side_by_side_rows=side_rows,
            added_text_blob="",
        )

    matcher = SequenceMatcher(None, old_lines, new_lines)
    similarity = round(matcher.ratio(), 4)

    side_rows: list[DiffSideRow] = []
    added_only: list[str] = []
    removed_only: list[str] = []
    unchanged_count = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                left_idx = i1 + offset
                right_idx = j1 + offset
                unchanged_count += 1
                side_rows.append(
                    DiffSideRow(
                        left_line_no=left_idx + 1,
                        left_text=old_lines[left_idx],
                        left_type="equal",
                        right_line_no=right_idx + 1,
                        right_text=new_lines[right_idx],
                        right_type="equal",
                    )
                )
        elif tag == "delete":
            for left_idx in range(i1, i2):
                removed_only.append(old_lines[left_idx])
                side_rows.append(
                    DiffSideRow(
                        left_line_no=left_idx + 1,
                        left_text=old_lines[left_idx],
                        left_type="delete",
                        right_line_no=None,
                        right_text="",
                        right_type="empty",
                    )
                )
        elif tag == "insert":
            for right_idx in range(j1, j2):
                added_only.append(new_lines[right_idx])
                side_rows.append(
                    DiffSideRow(
                        left_line_no=None,
                        left_text="",
                        left_type="empty",
                        right_line_no=right_idx + 1,
                        right_text=new_lines[right_idx],
                        right_type="insert",
                    )
                )
        elif tag == "replace":
            left_chunk = old_lines[i1:i2]
            right_chunk = new_lines[j1:j2]
            removed_only.extend(left_chunk)
            added_only.extend(right_chunk)
            max_span = max(len(left_chunk), len(right_chunk))
            for k in range(max_span):
                has_left = k < len(left_chunk)
                has_right = k < len(right_chunk)
                side_rows.append(
                    DiffSideRow(
                        left_line_no=(i1 + k + 1) if has_left else None,
                        left_text=left_chunk[k] if has_left else "",
                        left_type="delete" if has_left else "empty",
                        right_line_no=(j1 + k + 1) if has_right else None,
                        right_text=right_chunk[k] if has_right else "",
                        right_type="insert" if has_right else "empty",
                    )
                )

    # Build unified diff lines with line numbers
    unified_result: list[UnifiedDiffLine] = []
    raw_unified = list(
        unified_diff(
            old_lines,
            new_lines,
            fromfile="Previous Version",
            tofile="Current Version",
            lineterm="",
            n=3,
        )
    )
    old_cursor = 0
    new_cursor = 0
    for line in raw_unified:
        if line.startswith("---") or line.startswith("+++"):
            unified_result.append(UnifiedDiffLine(line_type="header", content=line))
        elif line.startswith("@@"):
            unified_result.append(UnifiedDiffLine(line_type="header", content=line))
            # Parse hunk header @@ -l,s +l,s @@
            try:
                parts = line.split(" ")
                old_start = abs(int(parts[1].split(",")[0]))
                new_start = abs(int(parts[2].split(",")[0]))
                old_cursor = old_start
                new_cursor = new_start
            except Exception:
                pass
        elif line.startswith("-"):
            unified_result.append(
                UnifiedDiffLine(
                    line_type="removed",
                    content=line[1:],
                    old_line_no=old_cursor,
                    new_line_no=None,
                )
            )
            old_cursor += 1
        elif line.startswith("+"):
            unified_result.append(
                UnifiedDiffLine(
                    line_type="added",
                    content=line[1:],
                    old_line_no=None,
                    new_line_no=new_cursor,
                )
            )
            new_cursor += 1
        elif line.startswith(" "):
            unified_result.append(
                UnifiedDiffLine(
                    line_type="context",
                    content=line[1:],
                    old_line_no=old_cursor,
                    new_line_no=new_cursor,
                )
            )
            old_cursor += 1
            new_cursor += 1

    summary = generate_change_summary(old_lines, new_lines, added_only, removed_only)
    stats = DiffStats(
        added_lines=len(added_only),
        removed_lines=len(removed_only),
        unchanged_lines=unchanged_count,
        similarity_ratio=similarity,
        has_meaningful_changes=bool(added_only or removed_only),
        summary=summary,
    )

    return ComputedDiff(
        stats=stats,
        unified_lines=unified_result,
        side_by_side_rows=side_rows,
        added_text_blob="\n".join(added_only),
    )
