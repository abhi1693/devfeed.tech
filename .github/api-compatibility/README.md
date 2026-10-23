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
the public, user, admin, and two MCP contract comparisons. The only finding
allowances are the nine exact tag PATCH documentation corrections described below.

The severity configuration follows the upstream
[oasdiff 1.33.0 severity documentation](https://github.com/oasdiff/oasdiff/blob/v1.33.0/docs/BREAKING-CHANGES.md#customizing-severity-levels).

## Removing response maximums

Removing an upper bound from a numeric response property is informational across
all endpoints and fields. For example, removing the `connector.max_pages` ceiling
allows an import connection to return any positive page limit without failing the
compatibility gate. The schema continues to describe the accepted values accurately.

Classifier probes cover nested and array-item response property maximum removal.
Scalar response body bounds, narrowing a request maximum, changing response types
and other checks retain their upstream severities.

## Correcting the tag PATCH input schema

Pydantic's `MISSING` sentinel lets partial tag edits omit unsupplied values from
serialization and accurately describes fields that reject null. The old runtime
already rejected null for names, slugs, aliases and automatic matching, and
enforced the same length, item-count and slug-pattern bounds. The generated
contract now exposes those rules directly instead of inside nullable unions.

[The correction file](tag-patch-corrections.md) permits only the exact method,
path, property, removed type and existing bound/pattern findings. It uses
[oasdiff's built-in documented correction mechanism](https://github.com/oasdiff/oasdiff/blob/v1.33.0/docs/BREAKING-CHANGES.md#ignoring-specific-breaking-changes).
Native probes compare the preserved baseline with the actual current Pydantic
schema, then introduce stricter limits, patterns, required fields and unrelated
nullability/method/endpoint changes to prove those still fail. Runtime tests
verify rejection rules as well as omission, empty aliases, false matching and
explicit topic unlinking. Retire the file and baseline when the old contract no
longer needs comparison; do not broaden these allowances for later API changes.

`python scripts/ci/api_contracts.py report` preserves each contract's unfiltered
findings under `reports/api-compatibility/raw/`. It overrides ignore files only
for this evidence report. The five native action checks still enforce `WARN`
against the shared configuration; raw reporting never replaces their gate.
