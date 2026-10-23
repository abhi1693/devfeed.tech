from devfeed_core.tag_topic_discovery import identity_keys


def test_identity_matching_normalizes_case_unicode_and_word_separators():
    assert identity_keys(["  Machine Learning ", "machine-learning", "MACHINE_learning"]) == [
        "machine-learning"
    ]
    assert identity_keys(["Ｐｙｔｈｏｎ", "Python", "PYTHON"]) == ["python"]


def test_identity_matching_preserves_language_punctuation_and_whole_names():
    assert len(identity_keys(["C", "C++", "C#", ".NET", "NET", "R", "Rust"])) == 7
    assert identity_keys(["Node.js"]) != identity_keys(["Nodejs"])
    assert identity_keys(["React"]) != identity_keys(["React Native"])
