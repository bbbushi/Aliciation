"""Temporary manual-test policy for Thoughts & Exploration only.

Set TEST_CONTROLS_ONLY to False to restore the ordinary controls/limits.
Stored production settings are never replaced with artificial large quotas.
This is a development-build policy, not a model-callable permission.
"""
TEST_CONTROLS_ONLY = True
TEST_IDLE_SECONDS = 30  # Rest behaviour; not a compulsory interval between work.


def active(context):
    return TEST_CONTROLS_ONLY and context.get("autonomy_test") is True


def source_limits(store):
    limits = dict(store.source_limits())
    if TEST_CONTROLS_ONLY:
        limits["chat_dependency_limit"] = None
    return limits
