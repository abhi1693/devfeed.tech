"""Project reviewed aggregate fixtures into each specialist's transport response."""

from types import SimpleNamespace


def article_step_client(*, complete, **attributes):
    def specialist_complete(prompt, schema):
        output = complete(prompt, schema)
        if not isinstance(output, dict) or not {
            "developer_relevance",
            "page_kind",
            "reasons",
        }.issubset(output):
            return output
        reasons = output.get("reasons") or []
        return {
            **{
                name: output.get(name)
                for name in schema["properties"]
                if name not in {"reason", "outcome"}
            },
            "outcome": output["outcome"],
            "reason": reasons[0] if reasons else "Supported by the supplied source.",
        }

    return SimpleNamespace(complete=specialist_complete, **attributes)
