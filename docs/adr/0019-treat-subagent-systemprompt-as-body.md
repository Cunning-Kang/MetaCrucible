# Treat subagent systemPrompt as body, not routing surface

Subagent capability artifacts split their frontmatter into routing surface fields that MetaCrucible will not automatically mutate (name, description, tools, spawns, output) and execution-parameter fields that may be revised (model, thinkingLevel, readSummarize, blocking, autoloadSkills). The systemPrompt field is treated as body even though it lives in frontmatter, so it can be optimized like a Skill body.
