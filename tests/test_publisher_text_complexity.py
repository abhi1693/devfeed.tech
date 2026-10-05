"""Malformed publisher text must not tie up ingestion in regex backtracking."""

import subprocess
import sys
import textwrap


def test_large_nonmatching_publisher_inputs_finish_within_a_bounded_process():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                from devfeed_aggregator.article_pages import publisher_redirect
                from devfeed_aggregator.languages import MAX_TEXT, prose
                from devfeed_core.feeds.fetcher import FetchResult, ImmediateRedirect

                assert prose('x' * 100_000) == 'x' * MAX_TEXT
                parser = ImmediateRedirect()
                parser.handle_starttag('meta', [
                    ('http-equiv', 'refresh'),
                    ('content', '0;url=' + ' ' * 100_000 + 'x\\ny'),
                ])
                assert parser.target is None
                html = (
                    '<head><meta http-equiv="refresh" content="0;url='
                    + ' ' * 100_000 + "'x'junk" + '"></head>'
                    + '<main><p>If not redirected, visit '
                    + '<a href="https://publisher.example/full">here</a>.</p></main>'
                )
                page = FetchResult(200, html.encode(), 'https://publisher.example/article',
                                  content_type='text/html')
                assert publisher_redirect(page) is None
                print('All publisher parsers completed')
                """
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "All publisher parsers completed"
