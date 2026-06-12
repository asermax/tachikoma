import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import { type InstallManager, isGitSource } from "./installs.ts";

const RESTART_NOTE = "The change takes effect after Tachikoma restarts.";

export const InstallPluginParams = Type.Object({
  source: Type.String({
    description:
      "Git URL to clone (https://, git@, ssh://, file:// or ending in .git) or a local path to a directory/file containing the extension module",
  }),
  alias: Type.String({
    description: "Install alias — lowercase letters, digits, and hyphens",
  }),
});

export const PluginAliasParams = Type.Object({
  alias: Type.String({ description: "Alias of an installed plugin (see list_installed_plugins)" }),
});

export const ListInstalledPluginsParams = Type.Object({});

export const handleInstallPlugin = async (
  manager: InstallManager,
  args: Static<typeof InstallPluginParams>,
): Promise<string> => {
  const record = await manager.install(args.source, args.alias);

  return [
    `Plugin '${args.alias}' installed successfully.`,
    `- Source: ${record.source}`,
    `- Path: ${record.path}`,
    RESTART_NOTE,
  ].join("\n");
};

export const handleUpdatePlugin = async (
  manager: InstallManager,
  args: Static<typeof PluginAliasParams>,
): Promise<string> => {
  const result = await manager.update(args.alias);

  if (result.status === "skipped") return `Plugin '${args.alias}' skipped: ${result.detail}`;

  return [`Plugin '${args.alias}' updated.`, result.detail, RESTART_NOTE].join("\n");
};

export const handleListInstalledPlugins = (manager: InstallManager): string => {
  const entries = Object.entries(manager.list());

  if (entries.length === 0) return "No plugins installed.";

  return [
    "# Installed Plugins",
    "",
    ...entries.flatMap(([alias, record]) => [
      `- **${alias}** (${isGitSource(record.source) ? "git" : "local"})`,
      `  Source: ${record.source}`,
      `  Path: ${record.path}`,
      `  Installed: ${record.installedAt}`,
      "",
    ]),
  ].join("\n");
};

export const handleUninstallPlugin = async (
  manager: InstallManager,
  args: Static<typeof PluginAliasParams>,
): Promise<string> => {
  await manager.uninstall(args.alias);

  return [`Plugin '${args.alias}' uninstalled.`, RESTART_NOTE].join("\n");
};

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

/** pi extension factory exposing the plugin install management tools to the agent. */
export const createPluginToolsFactory =
  (manager: InstallManager): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "install_plugin",
      label: "Install Plugin",
      description:
        "Install a third-party Tachikoma plugin from a git URL (cloned into the data dir) or a local path (loaded in place). The plugin is loaded on the next restart.",
      promptSnippet: "Install third-party Tachikoma plugins from git URLs or local paths",
      promptGuidelines: [
        "Use install_plugin when the user asks to add a Tachikoma plugin; tell them it activates after a restart.",
      ],
      parameters: InstallPluginParams,
      async execute(_toolCallId, params) {
        return textResult(await handleInstallPlugin(manager, params));
      },
    });

    pi.registerTool({
      name: "update_plugin",
      label: "Update Plugin",
      description:
        "Update an installed plugin: git installs are pulled from their source; local installs are always current. Updates apply on the next restart.",
      promptSnippet: "Update an installed plugin to its latest version",
      promptGuidelines: [
        "Use update_plugin with the alias from list_installed_plugins when the user wants a newer plugin version.",
      ],
      parameters: PluginAliasParams,
      async execute(_toolCallId, params) {
        return textResult(await handleUpdatePlugin(manager, params));
      },
    });

    pi.registerTool({
      name: "list_installed_plugins",
      label: "List Installed Plugins",
      description:
        "List plugins installed through install_plugin, with their alias, source, install path, and install time.",
      promptSnippet: "List installed third-party plugins",
      promptGuidelines: [
        "Check list_installed_plugins before install_plugin to avoid alias collisions.",
      ],
      parameters: ListInstalledPluginsParams,
      async execute() {
        return textResult(handleListInstalledPlugins(manager));
      },
    });

    pi.registerTool({
      name: "uninstall_plugin",
      label: "Uninstall Plugin",
      description:
        "Uninstall a plugin by alias: removes its install record and deletes cloned files (local sources are left untouched). The plugin stays active until restart.",
      promptSnippet: "Uninstall a third-party plugin by alias",
      promptGuidelines: [
        "Use uninstall_plugin when the user wants a plugin gone; mention it unloads fully on restart.",
      ],
      parameters: PluginAliasParams,
      async execute(_toolCallId, params) {
        return textResult(await handleUninstallPlugin(manager, params));
      },
    });
  };
