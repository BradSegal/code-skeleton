library(trialtools)

cohort <- arrow::read_parquet("derived/cohort.parquet")
effect <- estimate_effect(cohort)
saveRDS(effect, "derived/effect.rds")
