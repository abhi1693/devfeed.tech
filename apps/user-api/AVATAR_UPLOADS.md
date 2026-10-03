# Avatar uploads

`POST /v1/user/settings/profile/avatar` accepts a multipart `file` containing a JPEG,
PNG, or WebP image up to 5 MiB and 20 megapixels. `DELETE` removes the avatar. Both
endpoints require the account session, allowed origin, and CSRF token, and return
the updated profile. Uploads preserve other profile fields.

The user API normalizes orientation, crops a square, removes metadata, and stores
32, 64, 128, 256, and 512 pixel WebP variants. It stores no original image. Each
account owns five fixed keys under `avatars/<hashed-account-id>/`; replacement
writes those same keys and removal deletes them. Profile JSON stores the variant
URLs and uses the 256 pixel URL as `avatar_url`. No database migration is needed.

Set `DEVFEED_IMAGE_STORAGE_ENABLED=true` and configure the existing image-storage
endpoint, bucket, public URL, access key, and secret key. Compose passes these
settings to the user API. Credentials remain server-side. The public image host
must allow cross-origin image access for Dev Card downloads.

Avatar objects use `Cache-Control: public, max-age=0, must-revalidate` and a fresh
`?v=` URL revision after each upload. Avoid CDN rules that force an immutable cache
policy for the `avatars/` prefix. The account menu and public profile choose
appropriately sized variants, while older external avatar URLs still work.

Account row locks serialize uploads, removal, and profile edits. Existing image
bytes stay in memory during an operation so a failed storage write or database
commit can restore the previous objects. No backup objects or historical images
are retained. Restoration is best effort when storage itself is unavailable.
