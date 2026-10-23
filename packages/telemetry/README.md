# Telemetry route labels

Next request metrics and Faro view names use an application-specific catalog
generated from registered page/API files and shared route descriptors. The
catalog includes partner products, connections and account settings; identifiers
become placeholders such as `/products/:id/edit` and `/settings/:id/audience`.
Unknown routes use `unmatched`. API labels retain the declared operation,
for example `/v1/user/articles/:id/bookmark`.

Faro route-change events contain only the approved `route` attribute. Both the
browser and receiver validate view names and route-change labels against the
application's catalog. Other diagnostic metadata, logs, exception messages and
trace attributes retain their existing authentication-redaction policy.

Chrome and Edge derive their finite screen labels from the same reader
descriptors. Article, profile and collection screen names exclude slugs,
usernames, query values and OAuth request parameters; unsupported routes use
`/other`. The analytics relay accepts only those approved screen categories.

After page, API or route descriptor changes, run `npm run telemetry:generate`.
The application contract generators update this catalog too, and each app's
lint command rejects stale generated metadata. Run
`node --test tests/page-route-generator.test.mjs` for the generation regression.

Production bundle checks use `node apps/web/tests/browser/telemetry.mjs` for web,
adding `admin` or `partner` for those applications. For built extensions, run
`node --test --test-name-pattern="telemetry labels" apps/extensions/tests/reader.browser.mjs`,
adding `DEVFEED_EXTENSION_BROWSER=edge` for Edge. These checks use local collector
and API fixtures, inspect actual emitted labels, and verify that identifying
values never enter route labels.

## Dependency compatibility

The browser uses Grafana Faro Web Tracing 2.13, whose current stable release depends on the
OpenTelemetry 2.x SDK and requires API versions below 1.10. Keep the shared JavaScript telemetry
family on the latest compatible SDK 2.12/API 1.9.1 and experimental 0.223 releases. SDK 3.0/API 1.10
were evaluated for the 0.1.2 update but produce incompatible browser/server peer requirements.
Move this family together when Faro supports SDK 3. The server already uses the consolidated
`@opentelemetry/sdk-trace` package and its exporter-options constructor.
