# Daily Must Reads

Signed-in readers receive up to five articles from their prepared recommendation ranking.
The Gem button in the top navbar opens the selection at any time. On an eligible visit,
the selection opens automatically once per local calendar day. Existing dialogs,
onboarding, and article reading defer automatic presentation.

Desktop uses three columns and two rows: five article cards and a daily briefing tile.
Smaller selections leave out unavailable cards; tablet and phone layouts use two and
one columns. Readers can bookmark individual articles or save all unread picks.

The authenticated user API owns `GET /v1/user/must-reads?timezone=<IANA timezone>` and
`POST /v1/user/must-reads/presentation`. The reader supplies its browser timezone.
Snapshots persist ranked article IDs and explanations in `user_must_reads`, keyed by
account and local date. Recommendation refreshes do not replace today's selection.
Visibility, language, and content preferences are checked again on every fetch.
No public-feed fallback is presented as personalized content.

Presentation claims serialize against the account row and update only an unclaimed
snapshot for automatic presentation. Manual presentation remains available. Mutations
use the existing session, origin, and CSRF protection. Requests compute no recommendation
graph or AI content. Reading progress uses the existing account reading events.

Apply migration `0021` before starting the updated user API. The website and both
extensions share the navbar control, modal, and article actions. Browser regression
coverage lives in `scripts/testing/must-reads.mjs` and runs through the website reader
suite and the Chrome and Edge authentication suites.

## MCP access

The authenticated `get_my_must_reads(timezone="UTC")` tool uses `devfeed:read`
permission on the existing `/mcp` transport. Pass an IANA timezone such as
`Asia/Kolkata` to match the reader's calendar day. It returns the same stable daily
selection with article previews, recommendation reasons, read IDs, and preparation
status. It does not claim presentation, record article opens, or change streaks.
Agents cannot access the presentation endpoint, even with write permission.

## Admin inspection

The user Analysis page shows Today's Must Reads in place of its former top-five
ranked recommendation preview. `GET /v1/admin/users/{user_id}/must-reads` reads the
saved daily selection and reports its date, timezone, reasons, reading status, and
presentation timestamp. It shares publication and feed-preference filtering with
the reader. Admin inspection does not create a snapshot or claim presentation.
The default timezone follows the user's latest saved selection, then an explicit
appearance timezone, otherwise UTC. An optional IANA `timezone` query inspects
that zone's current date. A missing snapshot is reported as not generated.
