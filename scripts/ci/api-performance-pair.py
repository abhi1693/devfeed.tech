"""Measure base and head sequentially on one runner, preserving both outcomes."""

import argparse
import subprocess
from pathlib import Path


def prepare_samples(output: Path, repeat: int, *, reset: bool = False) -> dict[str, Path]:
    samples = {side: output / f"{side}-{repeat}" for side in ("base", "head")}
    for destination in samples.values():
        destination.mkdir(parents=True, exist_ok=True)
        status = destination / "exit-code.txt"
        if reset or not status.exists():
            status.write_text("incomplete\n")
    return samples


def run_pair(candidate: Path, baseline: Path, output: Path, repeat: int) -> int:
    samples = prepare_samples(output, repeat, reset=True)
    applications = {"base": baseline, "head": candidate}
    order = ("base", "head") if repeat % 2 == 0 else ("head", "base")
    failed = False
    for side in order:
        application = applications[side]
        destination = samples[side]
        with (destination / "runner.log").open("w") as log:
            try:
                result = subprocess.run(
                    [
                        str(candidate / ".venv/bin/python"),
                        str(candidate / "scripts/load-test.py"),
                        "--app-root",
                        str(application),
                        "--app-python",
                        str(application / ".venv/bin/python"),
                        "--users",
                        "16",
                        "--spawn-rate",
                        "4",
                        "--seconds",
                        "60",
                        "--rows",
                        "1000",
                        "--output",
                        str(destination),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                code = result.returncode
            except OSError as error:
                log.write(f"Unable to launch {side} sample: {error}\n")
                code = 127
        (destination / "exit-code.txt").write_text(f"{code}\n")
        failed = failed or code != 0
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, choices=range(3), required=True)
    parser.add_argument("--candidate-root", type=Path, default=Path("candidate"))
    parser.add_argument("--base-root", type=Path, default=Path("baseline"))
    parser.add_argument("--output", type=Path, default=Path("reports/performance"))
    parser.add_argument("--preserve-only", action="store_true")
    args = parser.parse_args()
    if args.preserve_only:
        prepare_samples(args.output.resolve(), args.repeat)
        return 0
    return run_pair(
        args.candidate_root.resolve(),
        args.base_root.resolve(),
        args.output.resolve(),
        args.repeat,
    )


if __name__ == "__main__":
    raise SystemExit(main())
