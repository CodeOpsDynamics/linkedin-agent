"""
Bridge script for the period before Phase 4 (two-way Telegram approval) is
built. Use this to manually process a candidate the daily pipeline surfaced.

Usage:
    python -m src.write_confirmed <candidate_id> <post|article>
    python -m src.write_confirmed <candidate_id> <post|article> --publish

Without --publish, this just prints the draft so you can eyeball it and
copy-paste manually. With --publish, it also pushes it live via the LinkedIn
API and drops the source link as a first comment (needs LINKEDIN_CLIENT_ID,
LINKEDIN_CLIENT_SECRET, LINKEDIN_REFRESH_TOKEN set -- see README).
"""
import sys
from src import state_store, writer, linkedin_publish


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return

    candidate_id = int(sys.argv[1])
    confirmed_type = sys.argv[2]
    should_publish = "--publish" in sys.argv

    if confirmed_type not in ("post", "article"):
        print("Second argument must be 'post' or 'article'.")
        return

    candidate = state_store.get_candidate(candidate_id)
    if not candidate:
        print(f"No candidate found with id {candidate_id}.")
        return

    print(f"Writing {confirmed_type} for: {candidate['title']}\n")
    state_store.mark_candidate_confirmed(candidate_id, confirmed_type)

    draft_text = writer.write_draft(candidate, confirmed_type)
    draft_id = state_store.add_draft(candidate_id, confirmed_type, draft_text)

    print("=" * 60)
    print(draft_text)
    print("=" * 60)
    print(f"\nSaved as draft #{draft_id}. Review before it goes anywhere.")

    first_comment = writer.suggest_first_comment_link(candidate)
    if first_comment:
        print(f"\nSuggested first comment (source credit): {first_comment}")

    if should_publish:
        confirm = input("\nType 'PUBLISH' to push this live on LinkedIn now: ")
        if confirm.strip() == "PUBLISH":
            access_token = linkedin_publish.get_access_token()
            post_urn = linkedin_publish.publish_post(draft_text, access_token=access_token)
            print(f"Published: {post_urn}")
            state_store.mark_draft_published(draft_id, post_urn)
            if first_comment:
                linkedin_publish.post_first_comment(post_urn, first_comment, access_token=access_token)
                print("Source link posted as first comment.")
        else:
            print("Skipped publishing.")


if __name__ == "__main__":
    main()
