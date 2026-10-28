"""Real native analytical publication for Overview HTTP domain tests."""

from devfeed_admin_api.overview_windows import current_window, recent_window
from devfeed_core.models import utcnow


def publish_section(publication, group="operations", days=30, *, window=None):
    """Publish real domain facts into the fixture's isolated ClickHouse database."""
    now = utcnow()
    window = current_window() if group == "operations" else window or recent_window(days, now)
    publication()
    if group != "operations":
        publication.publish_history()
    return publication.section(group, window)
