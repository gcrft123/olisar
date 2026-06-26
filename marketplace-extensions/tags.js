// Server Tags / FAQ — save the answers your server keeps repeating (rules, links,
// guides) and recall them with a command, or let Olisar answer from them in chat.
// All tags live in one per-guild KV object so the FAQ tool can search the whole set.

function normalize(s) {
  return String(s || "").trim().toLowerCase().replace(/\s+/g, " ");
}

async function loadTags() {
  return (await host.kv.get("tags")) || {};
}

function findTag(tags, query) {
  const q = normalize(query);
  if (!q) return null;
  if (tags[q]) return q; // exact
  const names = Object.keys(tags);
  const padded = " " + q + " ";
  // Match generously, both directions: a tag named "rules" should answer
  // "what are the rules", and "rul" should still find "rules".
  return (
    names.find((n) => padded.indexOf(" " + n + " ") >= 0) || // tag name appears as a word in the question
    names.find((n) => n.startsWith(q)) ||                     // question is a prefix of a tag
    names.find((n) => n.indexOf(q) >= 0) ||                   // question is a substring of a tag
    names.find((n) => normalize(tags[n].content).indexOf(q) >= 0) || // question matches a tag body
    null
  );
}

defineExtension({
  id: "tags",
  name: "Server Tags & FAQ",
  version: "1.0.1",
  category: "Community",
  description:
    "Save your server's canned answers (rules, links, guides) and recall them with /tag — " +
    "and Olisar answers from them in chat.",
  permissions: ["kv", "discord.reply"],
  systemNote:
    "This server keeps a tag/FAQ list of its own canned answers (rules, links, how-tos). " +
    "Use the server_faq tool whenever someone asks about something server-specific (rules, " +
    "links, setup, where to find X); answer in your own words from the tag it returns.",

  tools: [
    {
      name: "server_faq",
      description:
        "Look up this server's own saved tags/FAQ to answer a server-specific question " +
        "(rules, links, how-tos, where things are). Returns the matching tag's content.",
      parameters: {
        type: "object",
        properties: { query: { type: "string", description: "what the person is asking about" } },
        required: ["query"],
      },
      handler: async (args) => {
        const tags = await loadTags();
        const names = Object.keys(tags);
        if (!names.length) return "This server has no saved tags yet.";
        const hit = findTag(tags, args.query);
        if (!hit) return "No matching tag. Saved tags: " + names.join(", ") + ".";
        return "Tag **" + hit + "**: " + tags[hit].content;
      },
    },
  ],

  commands: [
    {
      name: "tag",
      description: "Recall a saved tag by name.",
      options: [{ name: "name", description: "the tag to show", type: "string", required: true }],
      handler: async (i) => {
        const tags = await loadTags();
        const hit = findTag(tags, i.options.name);
        if (!hit) {
          const names = Object.keys(tags);
          await i.reply({
            content: names.length
              ? "No tag like that. Try: " + names.map((n) => "`" + n + "`").join(", ")
              : "No tags saved yet — an admin can add one with /tagset.",
            ephemeral: true,
          });
          return;
        }
        await i.reply({ content: tags[hit].content });
      },
    },
    {
      name: "taglist",
      description: "List every saved tag.",
      handler: async (i) => {
        const tags = await loadTags();
        const names = Object.keys(tags).sort();
        await i.reply({
          embed: host.embed({
            title: "Server tags (" + names.length + ")",
            description: names.length ? names.map((n) => "`" + n + "`").join("  ") : "None yet.",
            color: 0x5865f2,
          }),
          ephemeral: true,
        });
      },
    },
    {
      name: "tagset",
      description: "Save or update a tag (server managers).",
      defaultMemberPermissions: "manage_guild",
      options: [
        { name: "name", description: "tag name (one word works best)", type: "string", required: true },
        { name: "content", description: "what the tag should say", type: "string", required: true },
      ],
      handler: async (i) => {
        const name = normalize(i.options.name);
        if (!name) return i.reply({ content: "Give the tag a name.", ephemeral: true });
        const tags = await loadTags();
        const existed = !!tags[name];
        tags[name] = { content: String(i.options.content), by: i.userId, at: Date.now() };
        await host.kv.set("tags", tags);
        await i.reply({ content: (existed ? "Updated" : "Saved") + " tag **" + name + "**.", ephemeral: true });
      },
    },
    {
      name: "tagdelete",
      description: "Delete a tag (server managers).",
      defaultMemberPermissions: "manage_guild",
      options: [{ name: "name", description: "the tag to delete", type: "string", required: true }],
      handler: async (i) => {
        const name = normalize(i.options.name);
        const tags = await loadTags();
        if (!tags[name]) return i.reply({ content: "No tag named **" + name + "**.", ephemeral: true });
        delete tags[name];
        await host.kv.set("tags", tags);
        await i.reply({ content: "Deleted tag **" + name + "**.", ephemeral: true });
      },
    },
  ],
});
