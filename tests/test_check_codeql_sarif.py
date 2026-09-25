import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_codeql_sarif", ROOT / "scripts" / "check_codeql_sarif.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_accepts_only_the_fixed_store_upload_flow():
    source = (ROOT / MODULE.EXPECTED_SOURCE).read_text()
    lines = source.splitlines()
    upload_line = next(
        index for index, line in enumerate(lines, start=1) if line.strip() == "body: archive,"
    )
    read_line = next(
        index
        for index, line in enumerate(lines, start=1)
        if "const chromeArchive = await readFile(" in line
    )
    finding = {
        "ruleId": MODULE.EXPECTED_RULE,
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": MODULE.EXPECTED_SOURCE},
                    "region": {"startLine": upload_line},
                }
            }
        ],
        "codeFlows": [
            {
                "threadFlows": [
                    {
                        "locations": [
                            {
                                "location": {
                                    "physicalLocation": {"region": {"startLine": read_line}}
                                }
                            },
                            {
                                "location": {
                                    "physicalLocation": {"region": {"startLine": upload_line}}
                                }
                            },
                        ]
                    }
                ]
            }
        ],
    }

    assert MODULE.is_expected_extension_upload(finding, source)


def test_rejects_an_unsuppressed_or_mislocated_upload_finding():
    source = (ROOT / MODULE.EXPECTED_SOURCE).read_text()
    finding = {
        "ruleId": MODULE.EXPECTED_RULE,
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": MODULE.EXPECTED_SOURCE},
                    "region": {"startLine": 1},
                }
            }
        ],
        "codeFlows": [],
    }

    assert not MODULE.is_expected_extension_upload(finding, source)
