"""Director Training: offline learning of the per-sport weights of OmniSportsSwitch (plan: docs/director-training.md).

OFFLINE ONLY. The live daemon must never import this package (a test greps daemon/*.py for it). This package may import `daemon`
(the scoring formula, the settings loader, the dataset builder) because it needs to use exactly the same numbers and metric code.

Modules: runs (run folders + experiment log), data (reading the logs), features (score components -> weight names), fit (per sport
logistic regression), evaluate (shadow evaluation of candidate weights), ship (turn a passing run into a settings patch), cli.
"""
