/**
 * Usage guidance for workspace git management, injected into the agent's context.
 * Scoped to main + background.
 */
export const GIT_USAGE = `## Git

Your workspace is a git repository, and commits are handled for you: when a session ends, all workspace changes (and any dirty project submodules) are committed with a generated message and pushed where a remote is configured. You do NOT need to commit or push by hand for normal work.

Your bash access to git is deliberately restricted — these are blocked at the tool layer and will fail: \`git push\`, \`git reset\`, \`git checkout .\`/\`git restore .\`, \`git clean\`, \`git remote\` mutations, \`git rebase\`, \`git filter-repo\`. Read-only git (\`status\`, \`log\`, \`diff\`, \`show\`, \`branch\`) and \`git clone\` stay available via bash. For the mutating operations, use the dedicated git tools instead (\`commit_workspace\` to commit now, \`scrub\` to purge paths from history — the workspace repo or a project under projects/).`;
