"""
One-time LinkedIn OAuth flow -- run this LOCALLY on your machine, once.

Prerequisites: LinkedIn Developer App created with "Sign In with LinkedIn
using OpenID Connect" + "Share on LinkedIn" products, and redirect URL
http://localhost:8765/callback added in the app's Auth tab.

Usage:
    export LINKEDIN_CLIENT_ID=xxx
    export LINKEDIN_CLIENT_SECRET=xxx
    python src/auth_flow.py

This will:
  1. Open your browser to LinkedIn's consent screen
  2. Catch the redirect on localhost:8765
  3. Exchange the auth code for access_token + refresh_token
  4. Store both in data/agent_state.db so linkedin_publish.py can use them

Run this again only if the refresh_token itself expires (~365 days) or you
change scopes.
"""
"""
One-time (well -- every ~55 days) LinkedIn OAuth flow -- run this LOCALLY.

IMPORTANT: standard-tier LinkedIn apps (Share on LinkedIn + Sign In with
OpenID Connect, which is what we have) do NOT receive a refresh_token --
that's only issued to Marketing Developer Platform partners, a much heavier
approval tier we don't need for personal posting. Standard apps get a
60-day access token and nothing else. LinkedIn confirms this is by design.

Practical implication: there's no way to auto-refresh silently. This script
gets you a fresh 60-day access token each time you run it, and you'll need
to re-run it roughly every 55 days (a reminder workflow nudges you --
see .github/workflows/token_reminder.yml).

Prerequisites: LinkedIn Developer App created with "Sign In with LinkedIn
using OpenID Connect" + "Share on LinkedIn" products approved, and redirect
URL http://localhost:8765/callback added in the app's Auth tab.

Usage:
    export LINKEDIN_CLIENT_ID=xxx
    export LINKEDIN_CLIENT_SECRET=xxx
    python src/auth_flow.py

This will:
  1. Open your browser to LinkedIn's consent screen
  2. Catch the redirect on localhost:8765
  3. Exchange the auth code for an access_token
  4. Print it for you to store as a GitHub secret (LINKEDIN_ACCESS_TOKEN)
"""
import os
import webbrowser
import secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

import requests

CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8765/callback"
SCOPES = "openid profile w_member_social"

_auth_code = {}


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        if "code" in params:
            _auth_code["code"] = params["code"][0]
            _auth_code["state"] = params.get("state", [""])[0]
            self.wfile.write(
                b"<h2>Success. You can close this tab and return to your terminal.</h2>"
            )
        else:
            error = params.get("error_description", ["unknown error"])[0]
            self.wfile.write(f"<h2>Error: {error}</h2>".encode())

    def log_message(self, format, *args):
        pass  # silence default HTTP server logging


def run():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET env vars first.")
        return

    state = secrets.token_urlsafe(16)
    auth_params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    }
    auth_url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(auth_params)

    print("Opening browser for LinkedIn consent...")
    print(f"If it doesn't open automatically, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8765), CallbackHandler)
    print("Waiting for LinkedIn redirect on http://localhost:8765/callback ...")
    while "code" not in _auth_code:
        server.handle_request()

    if _auth_code.get("state") != state:
        print("ERROR: state mismatch -- possible CSRF, aborting.")
        return

    print("Got auth code, exchanging for access token...")
    token_resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": _auth_code["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    token_resp.raise_for_status()
    data = token_resp.json()

    access_token = data.get("access_token", "")
    expires_in_days = round(data.get("expires_in", 0) / 86400)

    print("\n" + "=" * 60)
    print(f"SUCCESS -- token valid for ~{expires_in_days} days.")
    print("Do NOT commit this to git. Store it as a GitHub secret:")
    print("=" * 60)
    print(f"\nLINKEDIN_ACCESS_TOKEN = {access_token}\n")
    print("Add it here: repo Settings > Secrets and variables > Actions > New secret")
    print("Name: LINKEDIN_ACCESS_TOKEN")
    print("=" * 60)
    print(f"\nSet a reminder to re-run this script in ~{max(expires_in_days - 5, 1)} days,")
    print("or let the token_reminder.yml workflow nudge you on Telegram.")


if __name__ == "__main__":
    run()

