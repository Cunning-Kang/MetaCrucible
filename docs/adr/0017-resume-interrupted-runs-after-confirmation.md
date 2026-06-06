# Resume interrupted runs only after confirmation

MetaCrucible records enough state to resume interrupted optimization runs, but it does not silently resume them. When an unfinished run is detected, the CLI asks whether to resume so users do not accidentally continue from a stale or unwanted optimization state.
