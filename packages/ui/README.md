# Shared UI

`@devfeed/ui/notifications` is the admin Chimely inbox used by both frontends.
Import `@devfeed/theme/notifications.css` alongside the shared theme. The component
owns the popover, tabs, notification controls, badge, sound and connection lifecycle.
Each app supplies its authenticated client, safe navigation policy, labels and footer.
Session/CSRF handling and notification environments remain app-specific.

The admin resolves inherited category preferences before connecting. The user app
uses its own subscriber preferences. Both refresh on preference changes and reconnect
when the page becomes visible; hidden/unmounted inboxes close their connections.

Validation runs through both apps' lint, component tests and production builds.
The existing Chimely 0.2.2 tabbed list assigns `role=tabpanel` directly to its `ul`,
which triggers axe's `listitem` rule. This upstream semantic issue also affects the
original admin inbox; visual and other accessibility checks are tracked separately.
