from trialtools.normalise import normalise_group


def prepare_labels(labels: list[str]) -> list[str]:
    return [normalise_group(label) for label in labels]
