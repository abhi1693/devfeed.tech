# API compatibility policy

All HTTP and MCP contracts use the same `.oasdiff.yaml` configuration, both in CI
and in the local classifier regression probes. Response enum additions are
informational across every endpoint and enum value. Clients must tolerate unknown
response enum values and retain a fallback when rendering them.

The policy changes only the `response-property-enum-value-added` check. It keeps
oasdiff's other default severities, including its distinction between expanding
responses and narrowing valid requests. Removing endpoints or required response
fields, removing accepted request enum values, adding required request fields, and
changing response types still fail the `WARN` compatibility gate. Narrowing a
response enum keeps its upstream informational classification.

`python scripts/ci/api_contracts.py verify` exercises the pinned classifier with
compatible and breaking examples, including response enum additions in nested
objects, arrays, maps, and scalar responses. The probes use the same policy as
the public, user, admin, and two MCP contract comparisons. The sole endpoint exception is documented below; response enum additions have no value-specific ignore list.

The severity configuration follows the upstream
[oasdiff 1.33.0 severity documentation](https://github.com/oasdiff/oasdiff/blob/v1.33.0/docs/BREAKING-CHANGES.md#customizing-severity-levels).

## Approved partner import page-limit change

PR #240 deliberately removes the previous 1,000-page ceiling from connector settings.
This expands accepted requests and possible values returned by the admin connection
endpoints. Consumers must accept any positive integer for `connector.max_pages`.

`admin-page-limit-errors.txt` acknowledges exactly seven response findings: removal
of the old `1000.00` maximum for `max_pages` on the existing connection read/write
and logo responses. Only the admin CI comparison uses this file; local classifier
probes use the same file. Other fields, endpoints, numeric-bound changes, request
restrictions and response types retain their existing checks. The pinned classifier
probes verify that the approved removal passes while unrelated bound changes fail.

The exact finding descriptions follow the upstream [specific-change ignore format](https://github.com/oasdiff/oasdiff/blob/v1.33.0/docs/BREAKING-CHANGES.md#ignoring-specific-breaking-changes).
