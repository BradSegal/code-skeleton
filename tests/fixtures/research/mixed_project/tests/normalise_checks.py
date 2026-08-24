from trialtools.legacy import canonicalise_group
from trialtools.normalise import normalise_group


def test_public_and_legacy_names_agree() -> None:
    assert normalise_group(" Treatment A ") == "treatment-a"
    assert canonicalise_group(" Treatment A ") == "treatment-a"
