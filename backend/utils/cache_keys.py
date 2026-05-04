# utils/cache_keys.py

from django.core.cache import cache


# ─── Key Builders ───────────────────────────────────────────

def user_profile_key(user_id):
    return f"user:profile:{user_id}"

def user_workspaces_key(user_id):
    return f"user:workspaces:{user_id}"

def org_profile_key(org_id):
    return f"org:profile:{org_id}"

def org_members_key(org_id):
    return f"org:members:{org_id}"


# ─── Invalidation Helpers ────────────────────────────────────

def invalidate_user_cache(user_id):
    cache.delete_many([
        user_profile_key(user_id),
        user_workspaces_key(user_id),
    ])

def invalidate_org_cache(org_id):
    cache.delete_many([
        org_profile_key(org_id),
        org_members_key(org_id),
    ])
