// Timezone Coordinator — members save their timezone, then anyone can turn "8pm ET"
// into a Discord timestamp that shows *everyone* their own local time. Solves the
// "what time is that for me?" pain that global communities hit constantly.
//
// Discord timestamps (<t:unix:style>) auto-localize per viewer, so we just need to
// compute the right unix instant. DST-correct offsets come from timeapi.io (keyless).

const TZAPI = "https://timeapi.io/api/timezone/zone?timeZone=";

const ALIASES = {
  et: "America/New_York", est: "America/New_York", edt: "America/New_York",
  ct: "America/Chicago", cst: "America/Chicago", cdt: "America/Chicago",
  mt: "America/Denver", mst: "America/Denver", mdt: "America/Denver",
  pt: "America/Los_Angeles", pst: "America/Los_Angeles", pdt: "America/Los_Angeles",
  gmt: "Etc/GMT", utc: "Etc/UTC", bst: "Europe/London", cet: "Europe/Paris",
  cest: "Europe/Paris", ist: "Asia/Kolkata", jst: "Asia/Tokyo", aest: "Australia/Sydney",
};

function resolveZone(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  const lower = s.toLowerCase();
  if (ALIASES[lower]) return ALIASES[lower];
  return s; // assume an IANA name like America/New_York
}

// -> { offsetSec, y, mo, d } for "today" in the zone, or [null, errorString].
async function zoneInfo(zone) {
  let r;
  try {
    r = await host.fetch(TZAPI + encodeURIComponent(zone));
  } catch (e) {
    return [null, "Couldn't reach the time service."];
  }
  if (r.status === 400 || r.status === 404)
    return [null, "I don't recognize the timezone **" + zone + "**. Try an IANA name like `America/New_York` or `Europe/London`, or ET/PT/GMT."];
  if (!r.ok) return [null, "The time service returned an error."];
  let body;
  try {
    body = await r.json();
  } catch (e) {
    return [null, "The time service sent something unexpected."];
  }
  const off = body.currentUtcOffset && typeof body.currentUtcOffset.seconds === "number"
    ? body.currentUtcOffset.seconds : null;
  const local = String(body.currentLocalTime || "");
  const m = local.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (off === null || !m) return [null, "Couldn't read the current time for **" + zone + "**."];
  return [{ offsetSec: off, y: +m[1], mo: +m[2], d: +m[3] }, null];
}

// "8pm", "8:30 pm", "20:00", "0830" -> { h, m } or null.
function parseTime(raw) {
  const s = String(raw || "").trim().toLowerCase().replace(/\s+/g, "");
  let m = s.match(/^(\d{1,2})(?::(\d{2}))?(am|pm)$/);
  if (m) {
    let h = +m[1] % 12;
    if (m[3] === "pm") h += 12;
    return { h, m: m[2] ? +m[2] : 0 };
  }
  m = s.match(/^(\d{1,2}):(\d{2})$/) || s.match(/^(\d{2})(\d{2})$/);
  if (m) {
    const h = +m[1], mm = +m[2];
    if (h < 24 && mm < 60) return { h, m: mm };
  }
  return null;
}

// time string + zone -> unix seconds (next occurrence today/tomorrow), or [null, err].
async function toUnix(timeStr, zone) {
  const t = parseTime(timeStr);
  if (!t) return [null, "I couldn't read the time **" + timeStr + "**. Try `8pm`, `8:30pm`, or `20:00`."];
  const [info, err] = await zoneInfo(zone);
  if (err) return [null, err];
  let ms = Date.UTC(info.y, info.mo - 1, info.d, t.h, t.m, 0) - info.offsetSec * 1000;
  if (ms < Date.now() - 60000) ms += 86400000; // already past today -> mean tomorrow
  return [Math.floor(ms / 1000), null];
}

async function savedZone(userId) {
  const zones = (await host.kv.get("zones")) || {};
  return zones[userId] || null;
}

defineExtension({
  id: "timezone",
  name: "Timezone Coordinator",
  version: "1.0.0",
  category: "Community",
  description:
    "Members save their timezone; turn any time into a Discord timestamp that shows " +
    "everyone their own local time. Built for scheduling across a global community.",
  permissions: ["kv", "fetch", "discord.reply"],
  systemNote:
    "Members can save a timezone here. When someone asks 'what time is X for me/everyone' " +
    "or wants to coordinate a time across zones, use the convert_time tool — it returns a " +
    "Discord timestamp that auto-shows each reader their own local time.",

  tools: [
    {
      name: "convert_time",
      description:
        "Convert a clock time in some timezone into a Discord timestamp that auto-localizes " +
        "for every reader. Use for 'what time is 8pm ET for me/everyone' style questions. " +
        "If no zone is given, the asker's saved timezone is used.",
      parameters: {
        type: "object",
        properties: {
          time: { type: "string", description: "the time, e.g. '8pm' or '20:00'" },
          zone: { type: "string", description: "source timezone (IANA or ET/PT/GMT); optional" },
        },
        required: ["time"],
      },
      handler: async (args, ctx) => {
        let zone = resolveZone(args.zone);
        if (!zone) {
          const mine = await savedZone(ctx.userId);
          if (!mine) return "Tell me the source timezone (or save yours with /tzset first).";
          zone = mine;
        }
        const [unix, err] = await toUnix(args.time, zone);
        if (err) return err;
        return "That's <t:" + unix + ":F> (<t:" + unix + ":R>) — shown in each person's local time.";
      },
    },
  ],

  commands: [
    {
      name: "tzset",
      description: "Save your timezone so others can coordinate times with you.",
      options: [{ name: "zone", description: "IANA name (America/New_York) or ET/PT/GMT", type: "string", required: true }],
      handler: async (i) => {
        const zone = resolveZone(i.options.zone);
        const [, err] = await zoneInfo(zone);
        if (err) return i.reply({ content: err, ephemeral: true });
        const zones = (await host.kv.get("zones")) || {};
        zones[i.userId] = zone;
        await host.kv.set("zones", zones);
        await i.reply({ content: "Saved your timezone as **" + zone + "**.", ephemeral: true });
      },
    },
    {
      name: "when",
      description: "Turn a time into a timestamp everyone sees in their own local time.",
      options: [
        { name: "time", description: "e.g. 8pm, 8:30pm, 20:00", type: "string", required: true },
        { name: "zone", description: "source timezone (defaults to your saved one)", type: "string" },
      ],
      handler: async (i) => {
        let zone = resolveZone(i.options.zone);
        if (!zone) {
          zone = await savedZone(i.userId);
          if (!zone) return i.reply({ content: "Save your timezone first with /tzset, or pass a zone.", ephemeral: true });
        }
        const [unix, err] = await toUnix(i.options.time, zone);
        if (err) return i.reply({ content: err, ephemeral: true });
        await i.reply({
          content: "🕒 **" + i.options.time + "** (" + zone + ") is <t:" + unix + ":F> — <t:" + unix + ":R>",
        });
      },
    },
    {
      name: "tzlist",
      description: "Show the local time right now for everyone who saved a timezone.",
      handler: async (i) => {
        const zones = (await host.kv.get("zones")) || {};
        const ids = Object.keys(zones);
        if (!ids.length) return i.reply({ content: "Nobody has saved a timezone yet (try /tzset).", ephemeral: true });
        const now = Math.floor(Date.now() / 1000);
        const seen = {};
        const lines = [];
        for (const id of ids) {
          const z = zones[id];
          if (seen[z]) continue;
          seen[z] = true;
          lines.push("**" + z + "** — <t:" + now + ":t>");
        }
        await i.reply({
          embed: host.embed({ title: "Right now around the server", description: lines.join("\n"), color: 0x5865f2 }),
          ephemeral: true,
        });
      },
    },
  ],
});
