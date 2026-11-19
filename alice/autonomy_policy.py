"""Temporary manual-test policy for Thoughts, Exploration and web search.

TEST_CONTROLS_ONLY selects autonomous test policy and the default for new search settings.
Saved search_quota_testing is an independent user-controlled switch.
Stored production settings are never replaced with artificial large quotas.
Only the isolated Lab host activates the temporary strategy.
"""
TEST_CONTROLS_ONLY = False


def configure_lab(*, test_controls):
    global TEST_CONTROLS_ONLY
    TEST_CONTROLS_ONLY = test_controls is True


def active(context):
    return TEST_CONTROLS_ONLY and context.get("autonomy_test") is True


def source_limits(store):
    # SourceStore applies the saved search switch; do not override it again here.
    return dict(store.source_limits())


# Business quotas only. Never change permissions, source expiry or transport bounds.
SEARCH_QUOTA_KEYS = (
    "daily_queries", "daily_models", "daily_network", "daily_response_mib",
    "chat_search_calls", "chat_search_results", "chat_material_limit",
    "chat_dependency_limit", "read_candidates", "pool_capacity", "source_pool_bytes",
    "external_recall_limit", "tool_round_limit",
)


def search_quota_active(settings):
    return TEST_CONTROLS_ONLY and settings.get("search_quota_testing", False) is True


def search_settings(settings):
    """Effective in-memory search policy; saved production preferences stay intact."""
    result = dict(settings)
    if search_quota_active(settings):
        result.update(dict.fromkeys(SEARCH_QUOTA_KEYS))
    return result


def search_source_limits(limits, settings):
    result = dict(limits)
    if search_quota_active(settings):
        for key in ("chat_material_limit", "chat_dependency_limit", "source_pool_bytes", "external_recall_limit"):
            if key in result:
                result[key] = None
    return result
