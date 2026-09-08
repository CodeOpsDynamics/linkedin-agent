"""
One-time diagnostic -- run this LOCALLY (not part of the pipeline) to find
out, empirically, whether LinkedIn's Documents API (needed to auto-upload
carousel/document PDFs) is actually reachable on this app's tier.

Why this exists: search results on this are contradictory. Some sources say
document-post upload is included in the standard self-serve "Share on
LinkedIn" product (w_member_social). LinkedIn's own official docs show the
initializeUpload response returning an uploadUrl that contains the path
segment "ads-uploadedDocument" -- a strong signal this actually lives under
Marketing Developer Platform / ads tooling, the same heavier-approval tier
that turned out to block the Comments API earlier in this project. Rather
than build an entire PDF-generation + upload pipeline on an assumption and
find out at the last step that it 403s (same mistake pattern as before),
this makes ONE real API call and reports the actual result.

Usage:
    export LINKEDIN_ACCESS_TOKEN=xxx   # same token already in your GitHub secret
    python src/test_document_upload.py

This does NOT upload anything real -- initializeUpload only registers an
intent to upload and returns an uploadUrl + a document URN; nothing is
actually posted or made visible anywhere. Safe to run any time.
"""
import os
import requests
import json

ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN")


def get_person_urn(token: str) -> str:
    resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return f"urn:li:person:{resp.json()['sub']}"


def run():
    if not ACCESS_TOKEN:
        print("ERROR: set LINKEDIN_ACCESS_TOKEN first.")
        return

    print("Fetching person URN...")
    person_urn = get_person_urn(ACCESS_TOKEN)
    print(f"Person URN: {person_urn}")

    print("\nCalling Documents API initializeUpload (test only -- uploads nothing)...")
    resp = requests.post(
        "https://api.linkedin.com/rest/documents?action=initializeUpload",
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": "202601",
        },
        json={"initializeUploadRequest": {"owner": person_urn}},
    )

    print(f"\nSTATUS: {resp.status_code}")
    try:
        print("BODY:", json.dumps(resp.json(), indent=2))
    except ValueError:
        print("BODY (raw):", resp.text)

    print("\n" + "=" * 60)
    if resp.status_code == 200:
        print("RESULT: Documents API IS reachable on this app's current "
              "scopes. Auto-uploading carousel PDFs is genuinely buildable.")
    elif resp.status_code in (401, 403):
        print("RESULT: Documents API is BLOCKED on this app's tier "
              "(permission/scope error) -- same ceiling as the Comments "
              "API. Auto-upload isn't buildable without a heavier LinkedIn "
              "approval tier; the manual Canva-export bridge stays as-is.")
    else:
        print(f"RESULT: Unexpected status {resp.status_code} -- read the "
              "body above for the actual reason before concluding either way.")
    print("=" * 60)


if __name__ == "__main__":
    run()
