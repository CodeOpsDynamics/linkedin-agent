"""
LinkedIn Publisher -- Phase 3.

Prerequisites (one-time, manual, see README "LinkedIn App Setup"):
  1. Create an app at https://www.linkedin.com/developers/apps
  2. Add products: "Sign In with LinkedIn using OpenID Connect" + "Share on LinkedIn"
  3. Verify the app (requires an admin-verified company page -- create a
     placeholder page if you don't want to use a real one)
  4. Run src/auth_flow.py locally to get an access token, store it as a
     GitHub secret named LINKEDIN_ACCESS_TOKEN.

IMPORTANT constraint (confirmed against current LinkedIn docs): standard-tier
apps like this one do NOT receive a refresh_token -- that's exclusive to
Marketing Developer Platform partners, a heavier approval tier not needed
here. The access token is valid ~60 days and there is no way to silently
auto-refresh it. Re-run auth_flow.py roughly every 55 days and update the
GitHub secret manually. See .github/workflows/token_reminder.yml for a
nudge so this doesn't sneak up on you.

NOTE (flagged, unresolved): LinkedIn's native long-form "Articles" feature may
not be reachable through the public Posts API the same way regular posts are.
Validate this before assuming full parity -- if unsupported, the fallback is
publishing "articles" as long native text posts, which is common practice on
LinkedIn today regardless.
"""
import os
import requests

LINKEDIN_API_VERSION = "202601"  # LinkedIn versions APIs monthly, YYYYMM format
ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN")


def get_access_token() -> str:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("LINKEDIN_ACCESS_TOKEN not found")

    return token.strip()


def get_authenticated_person_urn(access_token: str) -> str:
    print("userinfo token:", repr(access_token[:20]) if access_token else "EMPTY")
    resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    sub = resp.json()["sub"]
    return f"urn:li:person:{sub}"


def publish_post(text: str, access_token: str = None) -> str:
    """Publishes a text post via the Posts API. Returns the created post URN.
    Pass in access_token if you already fetched one this run (e.g. to also
    call post_first_comment right after) to avoid redundant lookups."""
    access_token = access_token or get_access_token()

    print("=== LINKEDIN DEBUG ===")
    print("Token is None:", access_token is None)
    print("Token length:", len(access_token) if access_token else 0)
    print("Token start:", repr(access_token[:20]) if access_token else "EMPTY")

    author_urn = get_authenticated_person_urn(access_token)

    resp = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "author": author_urn,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        },
    )
    resp.raise_for_status()
    return resp.headers.get("x-restli-id", "unknown")


def post_first_comment(post_urn: str, comment_text: str, access_token: str = None):
    """Adds the source-link comment right after publishing. Per the algorithm
    rules baked into writer.py, links never go in the post body -- this is
    where the source credit belongs instead, without hurting reach."""
    access_token = access_token or get_access_token()
    author_urn = get_authenticated_person_urn(access_token)

    resp = requests.post(
        f"https://api.linkedin.com/rest/socialActions/{post_urn}/comments",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "actor": author_urn,
            "message": {"text": comment_text},
        },
    )
    resp.raise_for_status()
    return resp.json()
