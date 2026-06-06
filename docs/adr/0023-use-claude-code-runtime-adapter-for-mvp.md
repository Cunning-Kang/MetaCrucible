# Use Claude Code runtime adapter for the MVP

MetaCrucible evaluates real capability artifacts in an agent runtime, not bare model prompts. The MVP uses a Claude Code runtime adapter for both Skills and subagents, with OpenAI Agents SDK reserved as the first post-MVP runtime adapter; control-plane Optimizer, Judge, and Reviewer calls use direct model-provider APIs instead of the target runtime.

**Consequences**

- Skills are evaluated by materializing the candidate Skill into an isolated `.claude/skills/<name>/SKILL.md` tree and running Claude Code headlessly with explicit inputs.
- Subagents are evaluated by parsing the candidate subagent definition and injecting it through Claude Code's headless `--agents` mechanism.
- The target runtime adapter is separate from control-plane model provider configuration.
