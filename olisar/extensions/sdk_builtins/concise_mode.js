// Built-in SDK extension: concise mode. Behaviour-only — just a system note folded
// into the prompt when enabled (no tools, no permissions).
defineExtension({
  id: "concise_mode",
  name: "Concise mode",
  version: "1.0.0",
  category: "Behavior",
  description: "Olisar keeps replies short and to the point.",
  permissions: [],
  systemNote:
    "Keep your replies short and to the point — a sentence or two unless the " +
    "question genuinely needs more.",
});
