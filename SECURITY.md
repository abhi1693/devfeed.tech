# Security policy

## Supported releases

Security fixes target the latest published DevFeed release. Older releases do not
receive guaranteed backports; update to the latest release before reporting a
problem that has already been fixed. The `master` branch contains development work
and is not a substitute for a verified release.

## Report a vulnerability privately

Email **[desk.abhimanyu@gmail.com](mailto:desk.abhimanyu@gmail.com)** with the subject
**DevFeed security report**. Please do not report an unpatched vulnerability in a
public issue, discussion, pull request or social-media post.

Include:

- The affected release or commit and component.
- A description of the issue, its impact and any required access or conditions.
- Minimal reproduction steps or a proof of concept using test accounts and data.
- Redacted logs or screenshots, if useful, and a way to contact you.

Do not send passwords, session cookies, access tokens or other people's personal
information. If sensitive evidence is necessary, first ask how to share it safely.
Reports are reviewed by the maintainer; there is no guaranteed response or fix
deadline. Please allow time to investigate and coordinate disclosure before
publishing exploit details. There is no standing paid bug-bounty program.

## Testing boundaries

Use a local or self-hosted test instance whenever possible. Test only systems and
accounts you own or are explicitly authorized to assess. This policy does not
authorize scanning or exploiting production infrastructure or third-party services.

Do not access other users' data, modify or delete production data, bypass request
limits, run denial-of-service tests or use social engineering. If you encounter
private data accidentally, stop and report the issue without retaining or sharing
that data.

Relevant areas include authentication and authorization, session and CSRF handling,
feed-fetch SSRF protection, source and article rendering, secret exposure, queue
integrity and isolation between public, user and administration services.

## Fixes and disclosure

Confirmed fixes are documented in release notes or a security advisory with enough
information for operators to assess exposure and upgrade. Disclosure timing depends
on impact, available mitigations and coordination with affected upstream projects.
Researcher credit can be included with the reporter's agreement.

For ordinary bugs and feature requests, use
[GitHub issues](https://github.com/abhi1693/devfeed.tech/issues). For account-data or
privacy requests, see the [Privacy Policy](https://devfeed.tech/legal/privacy).
