# Native Fleet command contracts

`../smoke_fleet_contracts.py IMAGE COMPONENT` runs these commands in the actual
candidate image with a read-only root, the deployed UID/GID and disposable mounts.
It is called by the existing image smoke gate after its native-library checks.
The image job supplies the native architecture; running locally on AMD64 does not
establish ARM64 release-image readiness.

`commands.json` captures the prepared DevFeed Fleet commands and probes from the
home-lab checkout at revision `76bd4d280e00031e31b05f7c2b3e7fa15b1135c2`.
The prepared migration/backfill/publisher files can include uncommitted release
preparation; the recorded per-file SHA-256 values identify that exact input.
The consumer wrapper is the byte-identical `infra/chimely/consumer.py` from this
repository. CI never fetches the private infrastructure repository. Before final
deployment, compare each invoked command/probe and consumer payload with the
final Fleet files. Image-pin-only edits need not change command fixtures.

The driver owns an internal Docker network and uniquely named containers; it
removes them in `finally`. PostgreSQL uses `fleet_native_test`; Redis uses database 15. Only generated test certificates and obviously synthetic inbox/search
credentials are supplied. No host `DEVFEED_*` environment, authentication files or
production URLs are forwarded. Typesense uses the immutable official 30.2 index
already pinned in `compose.yaml`, covering native AMD64 and ARM64 fixtures.

The backend runs packaged Alembic to `0033`, seeds an article and account, executes
the exact migration hook, and verifies `0034` plus retained records. This includes
the actual CLI, migration files and hook connection settings. The trimmed admin,
user, partner and dedicated worker images deliberately use packaged
`Base.metadata.create_all()` for their separate runtime fixtures. Their output
labels that setup as metadata-only: it is not evidence that Alembic ran there.

The admin image executes the actual backfill hook, validates current pipeline
content and all 27 default report snapshots, and starts both publishers. Their
real probes must accept a fresh heartbeat and reject a stale one. The backend
starts every configured worker queue/group, verifies actual RQ registration and
heartbeats, runs the scheduler and its successful-cycle probe, and runs search
setup plus the indexer against real Typesense. Dedicated worker images start
their own commands and health probes. All API images execute their Fleet command
and must reject an incompatible database revision. Frontends start with their
writable cache/tmp mounts, and web also executes its preStop command.

CA initialization writes the combined trust bundle as UID/GID 10001 into an owned
Docker volume prepared as root:10001, mode 0770, matching the group-writable
emptyDir contract. The application loads it with native OpenSSL from a read-only
mount and must fail an attempted overwrite with EROFS. The volume is removed
after its containers. API/notification wrappers read role-scoped fake inbox files and
replace their process with the actual application command. Codex has its own
separate native driver and is not handled here.
