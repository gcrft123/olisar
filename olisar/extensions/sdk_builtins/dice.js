// Built-in SDK extension: dice roller. Authored against the Olisar SDK and shipped
// precompiled; seeded into the catalog as kind="builtin" (read-only in the editor).
defineExtension({
  id: "dice",
  name: "Dice roller",
  version: "1.0.0",
  category: "Fun",
  description: 'Olisar can roll dice on request — e.g. "roll 2d6+3".',
  permissions: [],
  tools: [{
    name: "roll_dice",
    description:
      "Roll dice in standard notation (e.g. 1d20, 2d6+3) and return the result. " +
      "Use when someone asks you to roll dice or flip for it.",
    parameters: {
      type: "object",
      properties: { notation: { type: "string", description: "dice notation, e.g. 2d6+3" } },
      required: ["notation"],
    },
    handler: function (args) {
      var bad = "I can roll dice like 1d20 or 2d6+3 (≤100 dice, ≤1000 sides).";
      var raw = String(args.notation || "1d6").replace(/\s/g, "").toLowerCase();
      var m = /^(\d*)d(\d+)([+-]\d+)?$/.exec(raw);
      if (!m) return bad;
      var count = parseInt(m[1] || "1", 10);
      var sides = parseInt(m[2], 10);
      var mod = parseInt(m[3] || "0", 10);
      if (count < 1 || count > 100 || sides < 2 || sides > 1000) return bad;
      var rolls = [];
      for (var i = 0; i < count; i++) rolls.push(1 + Math.floor(Math.random() * sides));
      var sum = rolls.reduce(function (a, b) { return a + b; }, 0) + mod;
      var detail = rolls.join(" + ");
      if (mod) detail += " " + (mod > 0 ? "+" : "-") + " " + Math.abs(mod);
      return args.notation + " → [" + detail + "] = " + sum;
    },
  }],
});
