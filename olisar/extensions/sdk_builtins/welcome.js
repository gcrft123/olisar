// Built-in SDK extension: welcome messages. Now a pure-SDK extension — it reacts to the
// memberJoin gateway event (host-dispatched to trusted extensions), generates a greeting
// in the server's persona voice (host.generate), and posts it to the configured channel
// (host.discord.send). The former bot/cogs/welcome.py is gone.
function fill(template, member) {
  return String(template || "")
    .split("{user}").join(member.displayName || "the new member")
    .split("{username}").join(member.username || "");
}

defineExtension({
  id: "welcome",
  name: "Welcome messages",
  version: "2.0.0",
  category: "Automation",
  description:
    "Greet new members in a channel you pick — in Olisar's voice, shaped by a " +
    "custom prompt you write (use {user} for the new member). Set the channel and " +
    "prompt on the Welcome panel.",
  // model.generate + discord.send are first-party-only capabilities (built-in/local).
  permissions: ["model.generate", "discord.send"],

  settingsSchema: {
    fields: [
      { key: "channel_id", type: "channel", label: "Channel" },
      {
        key: "prompt", type: "textarea", label: "Prompt",
        desc: "Layered on top of the persona — e.g. 'warmly welcome {user} and ask what brought them here', or 'roast {user} on their username'.",
      },
    ],
  },

  events: {
    async memberJoin(ctx) {
      const member = ctx.member || {};
      const cfg = (await host.settings.get()) || {};
      const channelId = cfg.channel_id;
      const prompt = String(cfg.prompt || "").trim();
      if (!channelId || !prompt) return; // not configured yet

      const instruction = fill(prompt, member);
      const task =
        "A new member just joined the server. Write a short welcome message for them, " +
        "staying fully in character. The new member is " + (member.displayName || "") +
        " (username " + (member.username || "") + "). Instruction for this welcome: " +
        instruction + "\nKeep it to 1-3 sentences. Output only the message.";

      const text = await host.generate({ task: task, maxTokens: 600 });
      if (!text) return;
      const mention = member.mention ? member.mention + " " : "";
      await host.discord.send(channelId, mention + text);
    },
  },
});
