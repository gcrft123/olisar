// Polls — a live, persistent poll: members click buttons to vote (one each, change
// anytime), the embed updates with running tallies, and the creator can close it.
// Unlike Discord's native poll, the buttons keep working indefinitely and across bot
// restarts (persistent components), and you control when it closes.

function bar(frac) {
  const n = Math.max(0, Math.min(10, Math.round(frac * 10)));
  return "▰".repeat(n) + "▱".repeat(10 - n);
}

function render(poll) {
  const counts = poll.opts.map(function () { return 0; });
  let total = 0;
  for (const u in poll.votes) {
    const idx = poll.votes[u];
    if (idx >= 0 && idx < counts.length) { counts[idx]++; total++; }
  }
  const lines = poll.opts.map(function (o, idx) {
    const c = counts[idx];
    const frac = total ? c / total : 0;
    return "**" + o + "**\n" + bar(frac) + "  " + c + " · " + Math.round(frac * 100) + "%";
  });
  const foot = poll.open
    ? "Click to vote — one vote each, click again to change."
    : "Poll closed.";
  return host.embed({
    title: (poll.open ? "📊 " : "🔒 ") + poll.q,
    description: lines.join("\n\n") + "\n\n*" + total + " vote" + (total === 1 ? "" : "s") + " — " + foot + "*",
    color: poll.open ? 0x5865f2 : 0x99aab5,
  });
}

function buttons(poll, pid) {
  const out = poll.opts.map(function (o, idx) {
    return { kind: "button", handlerId: "vote", arg: pid + "#" + idx,
             label: o.slice(0, 70), style: "secondary" };
  });
  out.push({ kind: "button", handlerId: "close", arg: pid, label: "🔒 Close", style: "danger" });
  return out;
}

defineExtension({
  id: "polls",
  name: "Polls",
  version: "1.0.0",
  category: "Community",
  description:
    "Create a live poll with clickable vote buttons and running tallies. Persistent — " +
    "votes keep working across restarts; the creator closes it when ready.",
  permissions: ["kv", "discord.reply", "discord.components"],

  commands: [
    {
      name: "poll",
      description: "Start a poll with up to 5 options.",
      options: [
        { name: "question", description: "what you're asking", type: "string", required: true },
        { name: "options", description: "choices, comma-separated (max 5)", type: "string", required: true },
      ],
      handler: async (i) => {
        const opts = String(i.options.options)
          .split(",").map(function (s) { return s.trim(); }).filter(Boolean).slice(0, 5);
        if (opts.length < 2) return i.reply({ content: "Give at least two comma-separated options.", ephemeral: true });
        const pid = String(Date.now());
        const poll = { q: String(i.options.question).slice(0, 240), opts, votes: {}, creator: i.userId, open: true };
        await host.kv.set("p:" + pid, poll);
        await i.reply({ embed: render(poll), components: buttons(poll, pid) });
      },
    },
  ],

  components: {
    vote: async (i) => {
      const hash = i.arg.lastIndexOf("#");
      const pid = i.arg.slice(0, hash);
      const idx = parseInt(i.arg.slice(hash + 1), 10);
      const poll = await host.kv.get("p:" + pid);
      if (!poll) return i.reply({ content: "That poll is gone.", ephemeral: true });
      if (!poll.open) return i.reply({ content: "That poll is closed.", ephemeral: true });
      if (poll.votes[i.userId] === idx) delete poll.votes[i.userId]; // click again = unvote
      else poll.votes[i.userId] = idx;                               // one vote per user
      await host.kv.set("p:" + pid, poll);
      await i.update({ embed: render(poll) });                       // live tally (keeps buttons)
    },
    close: async (i) => {
      const pid = i.arg;
      const poll = await host.kv.get("p:" + pid);
      if (!poll) return i.reply({ content: "That poll is gone.", ephemeral: true });
      if (i.userId !== poll.creator) return i.reply({ content: "Only the poll's creator can close it.", ephemeral: true });
      if (!poll.open) return i.reply({ content: "It's already closed.", ephemeral: true });
      poll.open = false;
      await host.kv.set("p:" + pid, poll);
      await i.update({ embed: render(poll), components: [] });       // final + remove buttons
    },
  },
});
