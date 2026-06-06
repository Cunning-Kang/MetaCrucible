# Do not automatically mutate routing surfaces

MetaCrucible does not automatically modify routing surfaces such as Skill frontmatter in the MVP. Static review may report routing-surface issues, but revisions are limited to safer mutable ranges so optimization cannot silently change how an agent runtime discovers or invokes a capability artifact.
