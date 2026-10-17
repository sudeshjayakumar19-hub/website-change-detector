"""RSS 2.0 feed generator for page-specific detected changes."""

from datetime import timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from app.models import MonitoredSite, PageSnapshot


def build_site_rss_xml(
    site: MonitoredSite,
    snapshots: list[PageSnapshot],
    base_url: str = "http://127.0.0.1:8000",
) -> str:
    """Generate a valid RSS 2.0 XML document for a monitored site's version history."""
    base = base_url.rstrip("/")
    history_link = f"{base}/sites/{site.id}/history"
    site_title = escape(f"Website Change Detector — {site.name}")
    site_desc = escape(f"Change history and snapshots for {site.url}")

    items_xml: list[str] = []
    for snap in snapshots:
        created_dt = snap.created_at
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        pub_date = escape(format_datetime(created_dt))
        item_title = escape(
            f"v{snap.version_number}: {snap.change_summary or 'Snapshot captured'}"
        )
        excerpt = (snap.extracted_text or "")[:600]
        if len(snap.extracted_text or "") > 600:
            excerpt += "..."
        desc_body = (
            f"Summary: {snap.change_summary or 'Baseline'}\n"
            f"SHA-256: {snap.content_hash}\n"
            f"Words: {snap.word_count} | Chars: {snap.char_count}\n"
        )
        if snap.matched_keywords:
            desc_body += f"Matched Keywords: {snap.matched_keywords}\n"
        desc_body += f"\nExcerpt:\n{excerpt}"

        item_link = f"{history_link}#version-{snap.version_number}"
        guid = escape(f"site-{site.id}-version-{snap.version_number}-{snap.content_hash[:16]}")

        items_xml.append(
            f"""    <item>
      <title>{item_title}</title>
      <link>{escape(item_link)}</link>
      <guid isPermaLink="false">{guid}</guid>
      <pubDate>{pub_date}</pubDate>
      <description>{escape(desc_body)}</description>
    </item>"""
        )

    items_block = "\n".join(items_xml)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{site_title}</title>
    <link>{escape(history_link)}</link>
    <description>{site_desc}</description>
    <generator>Website Change Detector v1.0</generator>
{items_block}
  </channel>
</rss>
"""
