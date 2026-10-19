# API compatibility policy

All HTTP and MCP contracts use the same `.oasdiff.yaml` configuration, both in CI
and in the local classifier regression probes. Response enum additions are
informational across every endpoint and enum value. Clients must tolerate unknown
response enum values and retain a fallback when rendering them.

The policy classifies `response-property-enum-value-added` and
`response-property-max-unset` as informational. It keeps
oasdiff's other default severities, including its distinction between expanding
responses and narrowing valid requests. Removing endpoints or required response
fields, removing accepted request enum values, adding required request fields, and
changing response types still fail the `WARN` compatibility gate. Narrowing a
response enum keeps its upstream informational classification.

`python scripts/ci/api_contracts.py verify` exercises the pinned classifier with
compatible and breaking examples, including response enum additions in nested
objects, arrays, maps, and scalar responses. The probes use the same policy as
the public, user, admin, and two MCP contract comparisons. There are no endpoint or finding-specific ignore lists.

The severity configuration follows the upstream
[oasdiff 1.33.0 severity documentation](https://github.com/oasdiff/oasdiff/blob/v1.33.0/docs/BREAKING-CHANGES.md#customizing-severity-levels).

## Removing response maximums

Removing an upper bound from a numeric response property is informational across
all endpoints and fields. For example, removing the `connector.max_pages` ceiling
allows an import connection to return any positive page limit without failing the
compatibility gate. The schema continues to describe the accepted values accurately.

Classifier probes cover nested and array-item response property maximum removal.
Scalar response body bounds, narrowing a request maximum, changing response types
and other checks retain their upstream severities. No endpoint-specific exception file is used.
