# Tag PATCH schema corrections

PR [#284](https://github.com/abhi1693/devfeed.tech/pull/284) adopts Pydantic's
`MISSING` sentinel. Its generated schema now reflects the existing runtime:
only `topic_id` accepts null, and the existing name, slug and alias limits
remain enforced. The previous schema placed bounds inside nullable unions;
oasdiff also reports moving those bounds to the non-null property as additions.

The baseline runtime at `ff71700d275c2a8b69a77642b543b1a87955404c` rejects all
nine corresponding inputs. These are documentation corrections, not new
request restrictions. `tag-patch-baseline.json` preserves that generated input
schema. Native classifier probes verify these exact findings are allowed while
new limits, patterns, required properties, nullability changes and changes to
another method or endpoint still fail. Current runtime regression tests retain
the rejection rules and intentional omitted, empty, false and unlink behavior.

The lines below use oasdiff's built-in method/path/description matching. Keep
every finding specific; do not replace them with endpoint-wide or check-wide
suppression. Raw findings remain in the API compatibility artifact. Remove
this file, its config entry and baseline probes when comparisons against the
old generated contract are retired; any future allowance needs its own review.

<!-- prettier-ignore -->
- PATCH /v1/admin/tags/{tag_id} request property `aliases` list-of-types was narrowed by removing types `null` from media type `application/json`
- PATCH /v1/admin/tags/{tag_id} request property `auto_link_topic` list-of-types was narrowed by removing types `null` from media type `application/json`
- PATCH /v1/admin/tags/{tag_id} request property `name` list-of-types was narrowed by removing types `null` from media type `application/json`
- PATCH /v1/admin/tags/{tag_id} request property `slug` list-of-types was narrowed by removing types `null` from media type `application/json`
- PATCH /v1/admin/tags/{tag_id} the `aliases` request property's maxItems was set to `100`
- PATCH /v1/admin/tags/{tag_id} the `name` request property's maxLength was set to `100`
- PATCH /v1/admin/tags/{tag_id} the `slug` request property's maxLength was set to `100`
- PATCH /v1/admin/tags/{tag_id} the `name` request property's minLength was set to `1`
- PATCH /v1/admin/tags/{tag_id} added the pattern `^[a-z0-9]+(?:-[a-z0-9]+)*$` to the request property `slug`
