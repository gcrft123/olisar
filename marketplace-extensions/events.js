// Events + RSVP — post an event card with Going / Maybe / Can't buttons. Members RSVP
// with a click, the attendee lists update live, and it all survives restarts. This is
// the most-requested kind of community bot (Apollo/Sesh) — Discord's native events have
// no real RSVP tracking. Tip: put a Discord timestamp in the "when" (e.g. <t:UNIX:F>)
// and pair with the Timezone extension's /when to get one.

const BUCKETS = ["going", "maybe", "no"];
const META = { going: "✅ Going", maybe: "❔ Maybe", no: "❌ Can't" };

function card(e) {
  function names(arr) { return arr.length ? arr.map(function (u) { return u.name; }).join(", ") : "—"; }
  return host.embed({
    title: (e.open ? "📅 " : "🗓️ ") + e.title,
    description: (e.desc ? e.desc + "\n\n" : "") + "**When:** " + e.when,
    color: e.open ? 0x57f287 : 0x99aab5,
    fields: BUCKETS.map(function (k) {
      return { name: META[k] + " (" + e.rsvp[k].length + ")", value: names(e.rsvp[k]), inline: false };
    }),
    footer: e.open ? "RSVP with the buttons below" : "Event closed",
  });
}

function buttons(id) {
  const out = BUCKETS.map(function (k) {
    return { kind: "button", handlerId: "rsvp", arg: id + "#" + k,
             label: META[k], style: k === "going" ? "success" : "secondary" };
  });
  out.push({ kind: "button", handlerId: "close", arg: id, label: "🔒 Close", style: "danger" });
  return out;
}

async function pushIndex(id, title) {
  const idx = (await host.kv.get("eindex")) || [];
  idx.push({ id, title });
  await host.kv.set("eindex", idx.slice(-50)); // keep the table bounded
}

defineExtension({
  id: "events",
  name: "Events & RSVP",
  version: "1.0.0",
  category: "Community",
  description:
    "Post an event with Going / Maybe / Can't RSVP buttons and live attendee lists — " +
    "persistent across restarts. The community scheduling bot, built in.",
  permissions: ["kv", "discord.reply", "discord.components"],
  systemNote:
    "This server can schedule events with RSVP. If someone asks what's coming up, suggest " +
    "they run /eventlist; to make one, /event.",

  commands: [
    {
      name: "event",
      description: "Post an event with RSVP buttons.",
      options: [
        { name: "title", description: "event name", type: "string", required: true },
        { name: "when", description: "when it is (a date/time, or a <t:..> timestamp)", type: "string", required: true },
        { name: "description", description: "optional details", type: "string" },
      ],
      handler: async (i) => {
        const id = String(Date.now());
        const e = {
          title: String(i.options.title).slice(0, 240),
          when: String(i.options.when).slice(0, 240),
          desc: i.options.description ? String(i.options.description).slice(0, 1000) : "",
          rsvp: { going: [], maybe: [], no: [] },
          creator: i.userId, open: true,
        };
        await host.kv.set("e:" + id, e);
        await pushIndex(id, e.title);
        await i.reply({ embed: card(e), components: buttons(id) });
      },
    },
    {
      name: "eventlist",
      description: "List the events still open for RSVP.",
      handler: async (i) => {
        const idx = (await host.kv.get("eindex")) || [];
        const lines = [];
        for (let n = idx.length - 1; n >= 0 && lines.length < 15; n--) {
          const e = await host.kv.get("e:" + idx[n].id);
          if (e && e.open) {
            lines.push("**" + e.title + "** — " + e.when + "  · ✅ " + e.rsvp.going.length + " · ❔ " + e.rsvp.maybe.length);
          }
        }
        await i.reply({
          embed: host.embed({
            title: "Upcoming events",
            description: lines.length ? lines.join("\n") : "No open events. Make one with /event.",
            color: 0x57f287,
          }),
          ephemeral: true,
        });
      },
    },
  ],

  components: {
    rsvp: async (i) => {
      const hash = i.arg.lastIndexOf("#");
      const id = i.arg.slice(0, hash);
      const state = i.arg.slice(hash + 1);
      const e = await host.kv.get("e:" + id);
      if (!e) return i.reply({ content: "That event is gone.", ephemeral: true });
      if (!e.open) return i.reply({ content: "That event is closed.", ephemeral: true });
      for (const k of BUCKETS) e.rsvp[k] = e.rsvp[k].filter(function (u) { return u.id !== i.userId; });
      (e.rsvp[state] || e.rsvp.going).push({ id: i.userId, name: i.displayName });
      await host.kv.set("e:" + id, e);
      await i.update({ embed: card(e) });
    },
    close: async (i) => {
      const e = await host.kv.get("e:" + i.arg);
      if (!e) return i.reply({ content: "That event is gone.", ephemeral: true });
      if (i.userId !== e.creator) return i.reply({ content: "Only the event's creator can close it.", ephemeral: true });
      if (!e.open) return i.reply({ content: "It's already closed.", ephemeral: true });
      e.open = false;
      await host.kv.set("e:" + i.arg, e);
      await i.update({ embed: card(e), components: [] });
    },
  },
});
