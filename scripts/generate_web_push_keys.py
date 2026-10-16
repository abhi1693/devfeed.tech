"""Write a fresh VAPID key pair to a private, untracked environment file."""

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="A new file outside the checkout"
    )
    parser.add_argument("--subject", required=True, help="Your mailto: contact address")
    args = parser.parse_args()
    if (
        not args.subject.startswith("mailto:")
        or "@" not in args.subject
        or any(char.isspace() or ord(char) < 33 or char in "'\"\\" for char in args.subject)
    ):
        parser.error("--subject must be a mailto: contact address")
    output = args.output.expanduser().resolve()
    checkout = Path(__file__).resolve().parents[1]
    if output.is_relative_to(checkout):
        parser.error("Write signing credentials outside the checkout")
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    private = key.private_numbers().private_value.to_bytes(32, "big")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = (
        "DEVFEED_WEB_PUSH_ENABLED=true\n"
        f"DEVFEED_WEB_PUSH_PUBLIC_KEY={encode(public)}\n"
        f"DEVFEED_WEB_PUSH_PRIVATE_KEY={encode(private)}\n"
        f"DEVFEED_WEB_PUSH_SUBJECT={args.subject}\n"
        "DEVFEED_WEB_PUSH_DELIVERY_HOUR=9\n"
        "DEVFEED_WEB_PUSH_SITE_URL=https://devfeed.tech\n"
    )
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error("The output file already exists; existing signing keys were preserved")
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
    print(f"Wrote private Web Push settings to {output}")


if __name__ == "__main__":
    main()
