# Shared UI

`@devfeed/ui/button`, `@devfeed/ui/input`, `@devfeed/ui/textarea`,
`@devfeed/ui/table`, and `@devfeed/ui/badge` provide the shared admin and partner
primitives. Button and badge also export their variant helpers; button exports
`ButtonProps`. Import these implementations directly from the shared package. Consumers
must load the shared theme tokens and include `packages/ui/src` in their Tailwind
sources, as both apps do in their global stylesheets.

`@devfeed/ui/choice-controls` provides Radix radio groups and a styled checkbox.
Import `@devfeed/ui/choice-controls.css` with the shared theme tokens. The Dev Card
editor uses these controls for keyboard-accessible design and content choices.

`@devfeed/ui/info-tip` pairs an info button with a Radix tooltip for supplementary
help. It opens on hover, focus, or tap. Import `@devfeed/ui/info-tip.css` with it.

`@devfeed/ui/theme-toggle` provides the light/dark/system theme button. Import
`@devfeed/theme/theme-toggle.css` for its styles. Reader and partner apps use
`useBrowserTheme` and `setBrowserTheme` from `@devfeed/ui/browser-theme` for local
preferences, system changes, and cross-tab updates. Render `themeScript` from
`@devfeed/ui/theme-script` in the document head before the body to apply the saved
palette before paint. Account-backed persistence remains in the consuming app.

`@devfeed/ui/notifications` is the admin Chimely inbox used by both frontends.
Import `@devfeed/theme/notifications.css` alongside the shared theme. The component
owns the popover, tabs, notification controls, badge, sound and connection lifecycle.
Each app supplies its authenticated client, safe navigation policy, labels and footer.
Session/CSRF handling and notification environments remain app-specific.

The admin resolves inherited category preferences before connecting. The user app
uses its own subscriber preferences. Both refresh on preference changes and reconnect
when the page is visible and focused; hidden, unfocused or unmounted inboxes close
their connections and stop refresh timers.

`@devfeed/ui/page-activity` provides the shared activity lifecycle for automatic
reads. It aborts each active scope and runs its cleanup on blur, visibility loss or
unmount, then starts a fresh scope on return. Admin polling, personalized-feed
polling and infinite scrolling use it to pause background work while preserving
displayed data. Explicit user writes are not cancelled by this lifecycle.

The package's `tsconfig.json` enables React's automatic JSX runtime for shared source files,
including editor diagnostics. From the repository root, run
`npm exec -- tsc --project packages/ui/tsconfig.json` to check the package directly.
CSS module declarations are included alongside the source.

Validation also runs through the consuming apps' lint, component tests and production builds.
The existing Chimely 0.2.2 tabbed list assigns `role=tabpanel` directly to its `ul`,
which triggers axe's `listitem` rule. This upstream semantic issue also affects the
original admin inbox; visual and other accessibility checks are tracked separately.
