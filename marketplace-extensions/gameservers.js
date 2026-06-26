// Game Server Status — live status for your community's OWN game servers, right in
// Discord: player counts, MOTD, who's online. Minecraft (Java + Bedrock) via the
// keyless mcsrvstat.us API, and FiveM via its public HTTP endpoints. Save a default
// server per guild so "is the server up?" / "who's on?" just work — in commands and
// in chat (Olisar calls the tools). Server-specific; not something you'd "just Google".

const MCSRV = "https://api.mcsrvstat.us/3/";
const MCSRV_BE = "https://api.mcsrvstat.us/bedrock/3/";

function clean(motd) {
  if (!motd) return "";
  const arr = motd.clean || motd.raw || [];
  return (Array.isArray(arr) ? arr.join("\n") : String(arr)).slice(0, 300);
}

// -> [normalized, error]. normalized: {online, title, version, players:{online,max,list}, motd}
async function queryMc(ip, bedrock) {
  let r;
  try { r = await host.fetch((bedrock ? MCSRV_BE : MCSRV) + encodeURIComponent(ip)); }
  catch (e) { return [null, "Couldn't reach the Minecraft status service."]; }
  if (!r.ok) return [null, "The status service returned an error."];
  let b;
  try { b = await r.json(); } catch (e) { return [null, "The status service sent something unexpected."]; }
  if (!b.online) return [{ online: false, title: ip }, null];
  const p = b.players || {};
  return [{
    online: true, title: b.hostname || ip, version: b.version || "",
    players: { online: p.online || 0, max: p.max || 0, list: (p.list || []).map(function (x) { return x.name; }) },
    motd: clean(b.motd),
  }, null];
}

async function queryFivem(ip) {
  const base = "http://" + ip.replace(/^https?:\/\//, "");
  let info, players;
  try {
    const ri = await host.fetch(base + "/info.json");
    if (!ri.ok) return [{ online: false, title: ip }, null];
    info = await ri.json();
    const rp = await host.fetch(base + "/players.json");
    players = rp.ok ? await rp.json() : [];
  } catch (e) { return [{ online: false, title: ip }, null]; }
  const vars = (info && info.vars) || {};
  return [{
    online: true, title: vars.sv_projectName || ip, version: (info && info.server) || "FiveM",
    players: { online: players.length, max: parseInt(vars.sv_maxClients || "0", 10) || 0,
               list: players.map(function (x) { return x.name; }) },
    motd: vars.sv_projectDesc || "",
  }, null];
}

async function query(ip, type) {
  type = String(type || "java").toLowerCase();
  if (type === "bedrock") return queryMc(ip, true);
  if (type === "fivem") return queryFivem(ip);
  return queryMc(ip, false); // java default
}

async function resolveTarget(ip, type) {
  if (ip) return [{ ip: String(ip), type: type || "java" }, null];
  const def = await host.kv.get("default");
  if (!def) return [null, "No server given and no default saved — set one with /serverset, or pass an address."];
  return [{ ip: def.ip, type: type || def.type || "java" }, null];
}

function statusLine(d) {
  if (!d.online) return "🔴 **" + d.title + "** is offline / unreachable.";
  return "🟢 **" + d.title + "** — " + d.players.online + "/" + d.players.max + " online" +
    (d.version ? " · " + d.version : "");
}

defineExtension({
  id: "gameservers",
  name: "Game Server Status",
  version: "1.0.0",
  category: "Gaming",
  description:
    "Live status for your community's Minecraft (Java/Bedrock) and FiveM servers — player " +
    "counts, MOTD, who's online — in commands and in chat. Save a default per server.",
  permissions: ["fetch", "kv", "discord.reply"],
  systemNote:
    "This server tracks the community's game servers. When someone asks if the server is up, " +
    "how many players are on, or who's online, use server_status / server_players (they fall " +
    "back to the saved default server when no address is given).",

  tools: [
    {
      name: "server_status",
      description:
        "Check whether the community's game server is up and how many players are on. " +
        "Uses the saved default server if no address is given.",
      parameters: {
        type: "object",
        properties: {
          address: { type: "string", description: "server address (optional; uses the saved default)" },
          type: { type: "string", description: "java | bedrock | fivem (optional)" },
        },
      },
      handler: async (args) => {
        const [t, err] = await resolveTarget(args.address, args.type);
        if (err) return err;
        const [d, qerr] = await query(t.ip, t.type);
        if (qerr) return qerr;
        return statusLine(d) + (d.online && d.motd ? "\n" + d.motd : "");
      },
    },
    {
      name: "server_players",
      description: "List who is currently online on the community's game server (saved default if no address).",
      parameters: {
        type: "object",
        properties: {
          address: { type: "string", description: "server address (optional)" },
          type: { type: "string", description: "java | bedrock | fivem (optional)" },
        },
      },
      handler: async (args) => {
        const [t, err] = await resolveTarget(args.address, args.type);
        if (err) return err;
        const [d, qerr] = await query(t.ip, t.type);
        if (qerr) return qerr;
        if (!d.online) return d.title + " is offline.";
        if (!d.players.list.length) return d.players.online + " online, but no player names are exposed.";
        return "Online now (" + d.players.online + "): " + d.players.list.join(", ");
      },
    },
  ],

  commands: [
    {
      name: "serverstatus",
      description: "Show a game server's live status.",
      options: [
        { name: "address", description: "server address (optional if a default is set)", type: "string" },
        { name: "type", description: "java | bedrock | fivem", type: "string" },
      ],
      handler: async (i) => {
        const [t, err] = await resolveTarget(i.options.address, i.options.type);
        if (err) return i.reply({ content: err, ephemeral: true });
        const [d, qerr] = await query(t.ip, t.type);
        if (qerr) return i.reply({ content: qerr, ephemeral: true });
        if (!d.online) {
          return i.reply({ embed: host.embed({ title: "🔴 " + t.ip, description: "Offline or unreachable.", color: 0xed4245 }) });
        }
        const sample = d.players.list.slice(0, 25).join(", ");
        await i.reply({
          embed: host.embed({
            title: "🟢 " + d.title,
            description: (d.motd ? d.motd + "\n\n" : "") +
              "**Players:** " + d.players.online + " / " + d.players.max +
              (d.version ? "\n**Version:** " + d.version : "") +
              (sample ? "\n**Online:** " + sample : ""),
            color: 0x57f287,
            footer: t.ip + " · " + t.type,
          }),
        });
      },
    },
    {
      name: "serverset",
      description: "Save this server's default game server (server managers).",
      defaultMemberPermissions: "manage_guild",
      options: [
        { name: "address", description: "server address (e.g. play.example.com)", type: "string", required: true },
        { name: "type", description: "java | bedrock | fivem (default java)", type: "string" },
      ],
      handler: async (i) => {
        const type = String(i.options.type || "java").toLowerCase();
        if (["java", "bedrock", "fivem"].indexOf(type) < 0)
          return i.reply({ content: "Type must be java, bedrock, or fivem.", ephemeral: true });
        await host.kv.set("default", { ip: String(i.options.address), type });
        await i.reply({ content: "Default server set to **" + i.options.address + "** (" + type + ").", ephemeral: true });
      },
    },
  ],
});
