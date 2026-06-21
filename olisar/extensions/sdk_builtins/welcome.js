// Built-in SDK extension: welcome messages. The on-join behaviour lives in
// bot/cogs/welcome.py (it reads ExtensionState.settings); this just declares the
// extension + its per-guild settings form, exactly like the old Python catalog entry.
defineExtension({
  id: "welcome",
  name: "Welcome messages",
  version: "1.0.0",
  category: "Automation",
  description:
    "Greet new members in a channel you pick — in Olisar's voice, shaped by a " +
    "custom prompt you write (use {user} for the new member). Set the channel and " +
    "prompt on the Welcome panel.",
  permissions: [],
  settingsSchema: {
    fields: [
      { key: "channel_id", type: "channel", label: "Channel" },
      {
        key: "prompt", type: "textarea", label: "Prompt",
        desc: "Layered on top of the persona — e.g. 'warmly welcome {user} and ask what brought them here', or 'roast {user} on their username'.",
      },
    ],
  },
});
