# Native Fleet command contracts

`../smoke_fleet_contracts.py IMAGE COMPONENT` runs these commands in the actual
candidate image with a read-only root, the deployed UID/GID and disposable mounts.
It is called by the existing image smoke gate after its native-library checks.
The image job supplies the native architecture; running locally on AMD64 does not
establish ARM64 release-image readiness.

`commands.json` records the supported DevFeed Fleet commands and probes, based on the
home-lab checkout at revision `76bd4d280e00031e31b05f7c2b3e7fa15b1135c2`.
The recorded per-file SHA-256 values identify the prepared baseline inputs. The
migration hook runs the PostgreSQL schema upgrade with a longer statement
timeout for migration connections. Runtime fixtures have no warehouse service
or credentials. Coordinate adapted commands with deployed Fleet manifests.
CI validates the API and worker entrypoints directly.

Generic worker commands use the lean `devfeed-worker` entrypoint with one physical
queue and unique-name arguments. The default queues are `high`, `default` and
`low`.
The native fixture also starts the isolated solver and a configured dedicated
ingestion queue. It avoids importing the administration CLI into each worker
parent. Dedicated extraction, images and discovery commands retain their existing
entrypoints and role credentials. Extraction and discovery fixtures supply an
exclusive `DEVFEED_JOB_QUEUE_OVERRIDES` route, as required by these optional
dedicated images; images retains its isolated default route. Before deploying
this change, update the owning Fleet manifests to the recorded commands and
physical queue configuration, provide
`DEVFEED_WORKER_HEALTH_PATH`, retain their parent-marker probes, and allow at least
26 minutes for worker termination. This covers the 1,500-second maximum job plus
bounded shutdown drains. Keep existing production resource requests and limits
until complete-job measurements justify changing them. The deployed Fleet probes
previously checked the parent marker by starting Python.
The native executable removes that interpreter startup while preserving marker
validation. Local probe timings do not establish a production CPU reduction.

The driver owns an internal Docker network and uniquely named containers; it
removes them in `finally`. PostgreSQL uses `fleet_native_test`; Redis uses database 15. Only generated test certificates and obviously synthetic search
credentials are supplied. No host `DEVFEED_*` environment, authentication files or
production URLs are forwarded. Typesense uses the immutable official 30.2 index
already pinned in `compose.yaml`, covering native AMD64 and ARM64 fixtures.

The backend runs packaged Alembic to `0033`, seeds an article and account, executes
the exact migration hook, and verifies the required schema revision plus retained records. This includes
the actual CLI, migration files and hook connection settings. The trimmed admin,
user, partner and dedicated worker images deliberately use packaged
`Base.metadata.create_all()` for their separate runtime fixtures. Their output
labels that setup as metadata-only: it is not evidence that Alembic ran there.

The backend starts the three priority workers
plus isolated solver and override scenarios, verifies each worker registers only
its expected physical queue and has a fresh heartbeat, runs the scheduler
and its successful-cycle probe, and runs search setup plus the indexer against
real Typesense. Dedicated worker images start
their own commands and health probes. All API images execute their Fleet command
and must reject an incompatible database revision. Frontends start with their
writable cache/tmp mounts, and web also executes its preStop command.
Frontend shutdown enables `DEBUG=next:start-server` and requires Next's
`next:start-server start-server process cleanup finished` message plus its
intentional SIGTERM exit status 143
after asynchronous cleanup. Backend processes must still exit 0. Unexpected
statuses, forced SIGKILL (137), and an OOM-killed container fail the smoke gate.

CA initialization writes the combined trust bundle as UID/GID 10001 into an owned
Docker volume prepared as root:10001, mode 0770, matching the group-writable
emptyDir contract. The application loads it with native OpenSSL from a read-only
mount and must fail an attempted overwrite with EROFS. The volume is removed
after its containers. Codex has its own
separate native driver and is not handled here.
