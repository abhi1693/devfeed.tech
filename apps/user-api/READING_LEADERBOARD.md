## Reading leaderboard

The user service exposes credential-free `GET /v1/user/leaderboard`, forwarded by
the reader at `/api/v1/leaderboard`. It returns two top-ten lists: `longest_streak`
uses the lifetime best consecutive UTC reading days; `reading_days` uses the
lifetime total of distinct UTC reading days. Existing authenticated article-open
activity supplies both totals. Anonymous activity is excluded.

Only claimed usernames with public profiles and positive totals qualify. Missing
profile visibility follows the existing public default. Rows contain rank,
username, public display name, avatar URL, and days; provider names, email addresses,
and account IDs are excluded. Equal scores share a competition rank (1, 1, 3), with
username ordering breaking display ties. Each list contains at most ten readers.

Authenticated `GET /v1/user/leaderboard/me` returns the caller's global rank in
each list, including ranks beyond the top ten, or null for an ineligible profile
or zero activity. It verifies the account's issuer, subject, organization, and ID.
Both endpoints and the web/extension reads bypass caching so subsequent requests
reflect profile privacy changes and deletion. Publish the user service and reader
together; the frontend shows an unavailable state if the new API is absent.

### Local sample

`npm run dev:seed` also creates twelve fictional public readers with claimed
`seed-` usernames, varied best streaks, tied scores, and different reading-day
rankings. Their daily history matches their aggregate streaks, so linked profiles
have a populated reading heatmap. Seed identities use the reserved `seed.invalid`
domain and contain no credentials or email addresses. Rerunning the seed preserves
existing accounts, activity, and local profile edits. The importer retains its
local Compose database guard.
