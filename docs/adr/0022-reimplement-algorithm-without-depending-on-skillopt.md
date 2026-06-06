# Reimplement the optimization algorithm without depending on SkillOpt

MetaCrucible implements its optimization loop and evaluation pipeline directly, using Microsoft SkillOpt as the algorithmic reference rather than a runtime dependency. The algorithmic layer follows SkillOpt-style reflection, rank-and-clip, edit budgeting, and validation gates, while the engineering flow follows GBrain's SkillOpt implementation patterns and Darwin-derived rubrics remain supplemental static review profiles.
