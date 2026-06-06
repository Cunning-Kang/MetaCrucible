# Separate evaluator roles from optimizer roles

MetaCrucible separates optimizer, target, and judge roles into independent execution contexts, even when they use the same model. This prevents same-context self-evaluation bias while keeping MVP model requirements practical.
