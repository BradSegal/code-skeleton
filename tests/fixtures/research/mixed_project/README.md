# Trialtools fixture

`trialtools.normalise_group()` owns cohort-key normalisation. Existing analysis
also imports `trialtools.legacy.canonicalise_group()` as an equivalent helper.

The Python preprocessing step produces a content-free cohort artifact consumed
by the exported R estimator. The Quarto report is public, but patient-level data
remain confidential and absent from this fixture.
