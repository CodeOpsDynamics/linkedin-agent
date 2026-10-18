import os
import requests
from urllib.parse import quote

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
        "LinkedIn-Version": "202601",
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
    encoded_post_urn = quote(post_urn, safe="")

    payload = {
        "actor": author_urn,
        "message": {
            "text": comment_text
        }
    }

    resp = requests.post(
        f"https://api.linkedin.com/rest/socialActions/{encoded_post_urn}/comments",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202601",
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

# ---------------------------------------------------------------------------
# Document (PDF carousel) posts -- Documents API, confirmed reachable on this
# app's tier via src/test_document_upload.py.
#
# Flow: initializeUpload -> PUT the PDF bytes -> poll until AVAILABLE ->
# create the post referencing the document URN. Kept well inside Vercel
# Hobby's 60s limit: polling is capped at DOC_POLL_TIMEOUT seconds.
# ---------------------------------------------------------------------------
import time

LINKEDIN_VERSION = "202601"
DOC_POLL_TIMEOUT = 25
DOC_POLL_INTERVAL = 2


def _rest_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }


def upload_document(pdf_bytes: bytes, owner_urn: str, access_token: str) -> str:
    """Uploads a PDF and returns its urn:li:document:... once processed."""
    init = requests.post(
        "https://api.linkedin.com/rest/documents?action=initializeUpload",
        headers=_rest_headers(access_token),
        json={"initializeUploadRequest": {"owner": owner_urn}},
        timeout=20,
    )
    if not init.ok:
        raise RuntimeError(f"Document initializeUpload {init.status_code}\n{init.text}")

    value = init.json().get("value", {})
    upload_url = value.get("uploadUrl")
    document_urn = value.get("document")
    if not upload_url or not document_urn:
        raise RuntimeError(f"initializeUpload returned no uploadUrl/document: {init.text}")

    put = requests.put(
        upload_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
        },
        data=pdf_bytes,
        timeout=30,
    )
    if not put.ok:
        raise RuntimeError(f"Document upload PUT {put.status_code}\n{put.text}")

    # Wait for LinkedIn to finish processing -- posting before AVAILABLE
    # can fail or show a broken document.
    encoded = quote(document_urn, safe="")
    deadline = time.time() + DOC_POLL_TIMEOUT
    status = "UNKNOWN"
    while time.time() < deadline:
        check = requests.get(
            f"https://api.linkedin.com/rest/documents/{encoded}",
            headers=_rest_headers(access_token),
            timeout=10,
        )
        if check.ok:
            status = check.json().get("status", "UNKNOWN")
            print("DOCUMENT STATUS:", status)
            if status == "AVAILABLE":
                return document_urn
            if status == "PROCESSING_FAILED":
                raise RuntimeError(f"LinkedIn failed to process the PDF: {check.text}")
        else:
            print("DOCUMENT STATUS CHECK:", check.status_code, check.text)
        time.sleep(DOC_POLL_INTERVAL)

    # Not confirmed AVAILABLE in time -- try posting anyway; LinkedIn often
    # accepts the post while processing completes.
    print(f"Document still {status} after {DOC_POLL_TIMEOUT}s, posting anyway")
    return document_urn


def publish_carousel(slides, caption: str, document_title: str,
                     access_token: str = None) -> str:
    """Renders slides -> PDF, uploads it, publishes a document post.
    Returns the post URN."""
    from src.carousel_pdf import render_pdf

    access_token = access_token or get_access_token()
    author_urn = get_authenticated_person_urn(access_token)

    pdf_bytes = render_pdf(slides, title=document_title)
    print(f"Rendered PDF: {len(slides)} slides, {len(pdf_bytes)} bytes")

    document_urn = upload_document(pdf_bytes, author_urn, access_token)

    payload = {
        "author": author_urn,
        "commentary": caption,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "media": {
                "title": (document_title or "Document")[:200],
                "id": document_urn,
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    resp = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=_rest_headers(access_token),
        json=payload,
        timeout=20,
    )
    print("===== CAROUSEL POST RESULT =====")
    print("STATUS:", resp.status_code)
    print("BODY:", resp.text)

    if not resp.ok:
        raise RuntimeError(f"LinkedIn {resp.status_code}\n{resp.text}")

    return resp.headers.get("x-restli-id", "unknown")
