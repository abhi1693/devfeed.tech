"""Run the same notification provisioning used by ordinary Compose startup."""

from compose_dev import compose


def provision() -> None:
    compose("up", "-d", "--wait", "chimely")
    compose("up", "--no-deps", "--exit-code-from", "chimely-provision", "chimely-provision")
    print("Chimely is ready; credentials are stored in scoped Docker volumes.", flush=True)


if __name__ == "__main__":
    provision()
