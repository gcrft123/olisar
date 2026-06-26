// Olisar extension sandbox — JS bootstrap.
//
// Loaded into a fresh QuickJS context BEFORE the author's (transpiled) extension
// code. It defines the two globals authors use — `defineExtension` and `host` — plus
// the private bridge the Python host drives.
//
// The bridge never calls Python directly (PyQuickJS forbids JS->Python callbacks
// while a CPU time-limit is armed). Instead every async capability pushes a request
// onto __OUTBOX and returns a pending Promise; the Python pump drains the outbox,
// performs the real work on the asyncio loop, and settles the Promise via __settle.
// `host.embed` is pure and stays synchronous.
(function (g) {
  "use strict";

  var SEQ = 0;
  g.__OUTBOX = [];
  g.__PENDING = {};

  // Queue an async capability call; resolve when the host settles it.
  function request(cap, method, args) {
    var id = ++SEQ;
    var p = new Promise(function (res, rej) { g.__PENDING[id] = { res: res, rej: rej }; });
    g.__OUTBOX.push({ id: id, cap: cap, method: method, args: args || [] });
    return p;
  }

  // ── Host capability surface (only granted methods succeed; the host enforces) ──
  function fetchImpl(url, init) {
    return request("fetch", "request", [String(url), init || {}]).then(function (r) {
      return {
        status: r.status,
        ok: r.status >= 200 && r.status < 300,
        headers: r.headers || {},
        text: function () { return Promise.resolve(r.body); },
        json: function () { return Promise.resolve(JSON.parse(r.body)); },
      };
    });
  }
  function cap(c, m) { return function () { return request(c, m, Array.prototype.slice.call(arguments)); }; }

  g.host = {
    fetch: fetchImpl,
    kb: { addSource: cap("kb", "addSource") },
    glossary: { add: cap("glossary", "add") },
    kv: { get: cap("kv", "get"), set: cap("kv", "set"), delete: cap("kv", "delete") },
    settings: { get: cap("settings", "get") },
    secret: cap("secret", "get"),
    embed: function (spec) { return { __embed: true, spec: spec || {} }; },
    log: cap("log", "write"),
  };

  // The interaction object handed to a slash-command handler. Data fields come from
  // the host; the methods round-trip through the outbox to the live discord.py call.
  function makeInteraction(data) {
    data = data || {};
    return {
      options: data.options || {},
      guildId: data.guildId, channelId: data.channelId,
      userId: data.userId, displayName: data.displayName,
      reply: function (p) { return request("discord", "reply", [p]); },
      followUp: function (p) { return request("discord", "followUp", [p]); },
      modal: function (spec) { return request("discord", "modal", [spec]); },
      awaitComponent: function (opts) { return request("discord", "awaitComponent", [opts || {}]); },
    };
  }

  // The interaction handed to a persistent component (button/select) handler. Like a
  // command interaction, but it edits the source message (update) and replies only
  // ephemerally to the one user who clicked.
  function makeComponentInteraction(data) {
    data = data || {};
    return {
      customId: data.customId, arg: data.arg, values: data.values,
      guildId: data.guildId, channelId: data.channelId, messageId: data.messageId,
      userId: data.userId, displayName: data.displayName,
      reply: function (p) { return request("discord", "reply", [p]); },
      update: function (p) { return request("discord", "update", [p || {}]); },
      deferUpdate: function () { return request("discord", "deferUpdate", []); },
    };
  }

  // ── Author entry point ──
  g.__SPEC = null;
  g.defineExtension = function (spec) { g.__SPEC = spec; };

  // ── Host-driven control surface (Python -> JS via eval only) ──

  // Drain queued capability requests (called each pump turn).
  g.__drainOutbox = function () { var o = JSON.stringify(g.__OUTBOX); g.__OUTBOX = []; return o; };

  // Settle a pending Promise with a JSON result (ok) or an error message.
  g.__settle = function (id, ok, payload) {
    var p = g.__PENDING[id];
    if (!p) return;
    delete g.__PENDING[id];
    if (ok) p.res(JSON.parse(payload)); else p.rej(new Error(payload));
  };

  // Manifest extraction (authoring/compile): the spec minus handler functions.
  g.__collectManifest = function () {
    var s = g.__SPEC;
    if (!s || !s.id) throw new Error("defineExtension was not called with an id");
    var tools = (s.tools || []).map(function (t) {
      return { name: t.name, description: t.description || "",
               parameters: t.parameters || { type: "object", properties: {} } };
    });
    var commands = (s.commands || []).map(function (c) {
      return { name: c.name, description: c.description || "", options: c.options || [],
               defaultMemberPermissions: c.defaultMemberPermissions || null,
               guildOnly: c.guildOnly !== false };
    });
    return JSON.stringify({
      manifest_version: 1, id: s.id, name: s.name || s.id, version: s.version || "1.0.0",
      category: s.category || "General", description: s.description || "",
      system_note: s.systemNote || "", default_enabled: !!s.defaultEnabled,
      permissions: s.permissions || [], tools: tools, commands: commands,
      seeds: s.seeds || {}, settings_schema: s.settingsSchema || { fields: [] },
      ui: s.ui || null, has_on_enable: typeof s.onEnable === "function",
      component_handlers: s.components ? Object.keys(s.components) : [],
    });
  };

  // Invoke a handler. Settles into __DONE/__RESULT/__ERROR which the pump polls.
  g.__invoke = function (kind, name, argsJson) {
    g.__DONE = false; g.__RESULT = null; g.__ERROR = null;
    var a = JSON.parse(argsJson || "{}");
    Promise.resolve().then(function () {
      var s = g.__SPEC;
      if (!s) throw new Error("extension did not call defineExtension");
      if (kind === "tool") {
        var t = (s.tools || []).filter(function (x) { return x.name === name; })[0];
        if (!t || typeof t.handler !== "function") throw new Error("no tool handler: " + name);
        return t.handler(a.args || {}, a.ctx || {});
      }
      if (kind === "command") {
        var c = (s.commands || []).filter(function (x) { return x.name === name; })[0];
        if (!c || typeof c.handler !== "function") throw new Error("no command handler: " + name);
        return c.handler(makeInteraction(a.interaction));
      }
      if (kind === "onEnable") {
        if (typeof s.onEnable !== "function") return null;
        return s.onEnable(a.ctx || {});
      }
      if (kind === "component") {
        var ch = (s.components || {})[name];
        if (typeof ch !== "function") throw new Error("no component handler: " + name);
        return ch(makeComponentInteraction(a.ctx || {}));
      }
      throw new Error("unknown invocation kind: " + kind);
    }).then(function (r) {
      g.__RESULT = JSON.stringify(r === undefined ? null : r);
    }).catch(function (e) {
      g.__ERROR = String((e && e.message) || e);
    }).finally(function () {
      g.__DONE = true;
    });
  };
})(globalThis);
