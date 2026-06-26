// Member Directory — members register what they can help with; then anyone (or Olisar
// in chat) can find "who in this server knows React / can design a logo / runs events".
// Server-specific knowledge you genuinely can't look up anywhere else.

async function loadDir() {
  return (await host.kv.get("dir")) || {};
}

function matches(dir, topic) {
  const q = String(topic || "").toLowerCase().trim();
  if (!q) return [];
  const terms = q.split(/[\s,]+/).filter(Boolean);
  const out = [];
  for (const id in dir) {
    const skills = String(dir[id].skills || "").toLowerCase();
    if (terms.some(function (t) { return skills.indexOf(t) >= 0; })) {
      out.push(dir[id]);
    }
  }
  return out;
}

defineExtension({
  id: "directory",
  name: "Member Directory",
  version: "1.0.0",
  category: "Community",
  description:
    "Members register their skills/expertise; find who can help with something via " +
    "/directory or just by asking Olisar in chat. A living 'who does what' for your server.",
  permissions: ["kv", "discord.reply", "discord.modal"],
  systemNote:
    "This server has a member skills directory. When someone asks who can help with a topic, " +
    "who knows X, or who to ask about Y, use the who_can_help tool and name the members it returns.",

  tools: [
    {
      name: "who_can_help",
      description:
        "Find members of THIS server who registered a skill matching a topic " +
        "(e.g. 'react', 'video editing', 'moderation'). Use for 'who can help with / who knows X' questions.",
      parameters: {
        type: "object",
        properties: { topic: { type: "string", description: "the skill or topic to find people for" } },
        required: ["topic"],
      },
      handler: async (args) => {
        const dir = await loadDir();
        if (!Object.keys(dir).length) return "No one has added skills to the directory yet (/skills set).";
        const hits = matches(dir, args.topic);
        if (!hits.length) return "No one in the directory listed anything matching '" + args.topic + "'.";
        return hits.slice(0, 10).map(function (m) { return m.name + " — " + m.skills; }).join("\n");
      },
    },
  ],

  commands: [
    {
      name: "skills",
      description: "Register what you can help with (opens a form).",
      handler: async (i) => {
        const form = await i.modal({
          title: "Your skills",
          fields: [{ id: "skills", label: "What can you help with?", style: "paragraph", required: true }],
        });
        const dir = await loadDir();
        dir[i.userId] = { name: i.displayName, skills: String(form.skills || "").slice(0, 500) };
        await host.kv.set("dir", dir);
        await i.followUp({ content: "Added you to the member directory ✅" });
      },
    },
    {
      name: "skillsclear",
      description: "Remove yourself from the member directory.",
      handler: async (i) => {
        const dir = await loadDir();
        if (!dir[i.userId]) return i.reply({ content: "You're not in the directory.", ephemeral: true });
        delete dir[i.userId];
        await host.kv.set("dir", dir);
        await i.reply({ content: "Removed you from the directory.", ephemeral: true });
      },
    },
    {
      name: "directory",
      description: "Find members who can help with a topic.",
      options: [{ name: "topic", description: "skill or topic, e.g. 'react'", type: "string", required: true }],
      handler: async (i) => {
        const dir = await loadDir();
        const hits = matches(dir, i.options.topic);
        await i.reply({
          embed: host.embed({
            title: "Who can help with '" + i.options.topic + "'",
            description: hits.length
              ? hits.slice(0, 15).map(function (m) { return "**" + m.name + "** — " + m.skills; }).join("\n")
              : "No matches. Members can add themselves with /skills.",
            color: 0xeb459e,
          }),
          ephemeral: true,
        });
      },
    },
  ],
});
