#' Estimate a deliberately small fixture effect
#' @export
estimate_effect <- function(frame) {
  model <- stats::lm(outcome ~ treatment, data = frame)
  structure(coef(model)[["treatment"]], class = "trial_effect")
}

print.trial_effect <- function(x, ...) print(unclass(x))

dynamic_helper <- function(name, frame) get(name)(frame)
