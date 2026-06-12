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
  alias: Type.String({
    description: "Alias of an installed extension (see list_installed_extensions)",
  }),
});

export const ListInstalledPluginsParams = Type.Object({});

export const handleInstallExternalExtension = async (
  manager: InstallManager,
  args: Static<typeof InstallPluginParams>,
): Promise<string> => {
  const record = await manager.install(args.source, args.alias);

  return [
    `ExternalExtension '${args.alias}' installed successfully.`,
    `- Source: ${record.source}`,
    `- Path: ${record.path}`,
    RESTART_NOTE,
  ].join("\n");
};

export const handleUpdateExternalExtension = async (
  manager: InstallManager,
  args: Static<typeof PluginAliasParams>,
): Promise<string> => {
  const result = await manager.update(args.alias);

  if (result.status === "skipped")
    return `ExternalExtension '${args.alias}' skipped: ${result.detail}`;

  return [`ExternalExtension '${args.alias}' updated.`, result.detail, RESTART_NOTE].join("\n");
};

export const handleListInstalledPlugins = (manager: InstallManager): string => {
  const entries = Object.entries(manager.list());

  if (entries.length === 0) return "No external extensions installed.";

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

export const handleUninstallExtension = async (
  manager: InstallManager,
  args: Static<typeof PluginAliasParams>,
): Promise<string> => {
  await manager.uninstall(args.alias);

  return [`ExternalExtension '${args.alias}' uninstalled.`, RESTART_NOTE].join("\n");
};

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

/** pi extension factory exposing the extension install management tools to the agent. */
export const createExternalToolsFactory =
  (manager: InstallManager): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "install_extension",
      label: "Install ExternalExtension",
      description:
        "Install a third-party Tachikoman extension from a git URL (cloned into the data dir) or a local path (loaded in place). The external extension is loaded on the next restart.",
      promptSnippet: "Install third-party Tachikoman extensions from git URLs or local paths",
      promptGuidelines: [
        "Use install_extension when the user asks to add a Tachikoman extension; tell them it activates after a restart.",
      ],
      parameters: InstallPluginParams,
      async execute(_toolCallId, params) {
        return textResult(await handleInstallExternalExtension(manager, params));
      },
    });

    pi.registerTool({
      name: "update_extension",
      label: "Update ExternalExtension",
      description:
        "Update an installed extension: git installs are pulled from their source; local installs are always current. Updates apply on the next restart.",
      promptSnippet: "Update an installed extension to its latest version",
      promptGuidelines: [
        "Use update_extension with the alias from list_installed_extensions when the user wants a newer extension version.",
      ],
      parameters: PluginAliasParams,
      async execute(_toolCallId, params) {
        return textResult(await handleUpdateExternalExtension(manager, params));
      },
    });

    pi.registerTool({
      name: "list_installed_extensions",
      label: "List Installed Plugins",
      description:
        "List external extensions installed through install_extension, with their alias, source, install path, and install time.",
      promptSnippet: "List installed third-party extensions",
      promptGuidelines: [
        "Check list_installed_extensions before install_extension to avoid alias collisions.",
      ],
      parameters: ListInstalledPluginsParams,
      async execute() {
        return textResult(handleListInstalledPlugins(manager));
      },
    });

    pi.registerTool({
      name: "uninstall_extension",
      label: "Uninstall ExternalExtension",
      description:
        "Uninstall an extension by alias: removes its install record and deletes cloned files (local sources are left untouched). The external extension stays active until restart.",
      promptSnippet: "Uninstall a third-party extension by alias",
      promptGuidelines: [
        "Use uninstall_extension when the user wants an extension gone; mention it unloads fully on restart.",
      ],
      parameters: PluginAliasParams,
      async execute(_toolCallId, params) {
        return textResult(await handleUninstallExtension(manager, params));
      },
    });
  };
