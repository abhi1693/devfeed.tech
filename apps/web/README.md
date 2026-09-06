# Reader application (planned)

Future developer discovery UI. Consume `/v1/feed`, `/v1/articles/{id}`,
`/v1/categories`, `/v1/tags` and `/v1/sources`. No reader account is required.
Store selected/blocked tags and sources in browser storage and send them as feed filters.
Use `/v1/categories/tree` for nested navigation. `/v1/tags` returns tag records with
IDs, display names, slugs and optional category IDs; article tags remain slug strings.
