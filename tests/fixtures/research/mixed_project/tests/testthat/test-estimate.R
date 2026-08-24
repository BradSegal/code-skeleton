test_that("effect retains its reviewable class", {
  frame <- data.frame(outcome = c(0, 1), treatment = c(0, 1))
  expect_s3_class(estimate_effect(frame), "trial_effect")
})
