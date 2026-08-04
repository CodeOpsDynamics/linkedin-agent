import os
import requests

ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN")


def get_access_token() -> str:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")

    if not token:
        raise RuntimeError("LINKEDIN_ACCESS_TOKEN not found")

    return token.strip()


def get_authenticated_person_urn(access_token: str) -> str:
    print("===== USERINFO =====")
    print("Token first 20:", repr(access_token[:20]))

    resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
    )

    print("USERINFO STATUS:", resp.status_code)
    print("USERINFO BODY:", resp.text)

    resp.raise_for_status()

    sub = resp.json()["sub"]

    author = f"urn:li:person:{sub}"

    print("AUTHOR URN:", author)

    return author


def publish_post(text: str, access_token: str = None) -> str:
    access_token = access_token or get_access_token()

    print("===== TOKEN =====")
    print("Token exists:", access_token is not None)
    print("Token length:", len(access_token))
    print("Token first 20:", repr(access_token[:20]))

    author_urn = get_authenticated_person_urn(access_token)

    payload = {
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
    }

    print("===== PAYLOAD =====")
    print(payload)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        # "LinkedIn-Version": "202601",   # disabled temporarily
    }

    resp = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=headers,
        json=payload,
    )

    print("===== POST RESULT =====")
    print("STATUS:", resp.status_code)
    print("HEADERS:", dict(resp.headers))
    print("BODY:", resp.text)

    if not resp.ok:
        raise RuntimeError(
            f"LinkedIn {resp.status_code}\n{resp.text}"
        )

    return resp.headers.get("x-restli-id", "unknown")


def post_first_comment(post_urn: str, comment_text: str, access_token: str = None):
    access_token = access_token or get_access_token()

    author_urn = get_authenticated_person_urn(access_token)

    payload = {
        "actor": author_urn,
        "message": {
            "text": comment_text
        }
    }

    resp = requests.post(
        f"https://api.linkedin.com/rest/socialActions/{post_urn}/comments",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json=payload,
    )

    print("===== COMMENT RESULT =====")
    print(resp.status_code)
    print(resp.text)

    if not resp.ok:
        raise RuntimeError(
            f"LinkedIn Comment Error {resp.status_code}\n{resp.text}"
        )

    return resp.json()