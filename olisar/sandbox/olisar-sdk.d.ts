/**
 * Olisar Extension SDK — author-facing surface.
 *
 * Write your extension by calling `defineExtension({...})` exactly once. You never
 * import anything: `defineExtension` and `host` are provided by the runtime. Your
 * code runs in a secure sandbox with no access to the network, filesystem, or the
 * bot's internals except through the `host` capabilities you request in `permissions`.
 */

/** Capabilities an extension may request. The operator approves these on save. */
type Permission =
  | "fetch"            // host.fetch — call external HTTP(S) APIs
  | "kb.write"         // host.kb.addSource — add knowledge-base sources
  | "glossary.write"   // host.glossary.add — add glossary facts
  | "kv"               // host.kv — per-guild key/value storage
  | "discord.reply"    // reply / follow up to a slash command
  | "discord.modal"    // pop a modal form during a command
  | "discord.components" // buttons / select menus during a command
  | "discord.send"     // host.discord.send — post a message to a channel (no @mentions for third-party)
  | "model.generate"   // host.generate — generate text in the persona voice (uses the operator's model quota)
  | `secret:${string}`; // host.secret("uex_api_key") — read an approved host secret (built-in/local only)

type JSONSchema = {
  type: "object" | "string" | "number" | "integer" | "boolean" | "array";
  description?: string;
  properties?: Record<string, JSONSchema>;
  required?: string[];
  items?: JSONSchema;
  enum?: string[];
};

/** Context passed to an LLM tool handler. */
interface ToolCtx {
  guildId: string;
  channelId: string;
  userId: string;
  displayName: string;
}

/** An LLM function-calling tool the model can invoke while your extension is on. */
interface ToolDef {
  name: string;
  description: string;
  parameters: JSONSchema;
  /** Return a short string for the model. May be async. */
  handler(args: Record<string, any>, ctx: ToolCtx): Promise<string> | string;
}

/** A slash-command option (maps to a Discord application-command option). */
interface OptionDef {
  name: string;
  description: string;
  type?: "string" | "integer" | "number" | "boolean" | "user" | "channel";
  required?: boolean;
}

/** Fields for a modal form popped with `interaction.modal(...)`. */
interface ModalSpec {
  title: string;
  fields: { id: string; label: string; style?: "short" | "paragraph"; required?: boolean }[];
}

interface EmbedSpec {
  title?: string;
  description?: string;
  url?: string;
  color?: number;
  fields?: { name: string; value: string; inline?: boolean }[];
  footer?: string;
  thumbnail?: string;
  image?: string;
}

/**
 * A button or select menu. Use `handlerId` (a key of your `components` map) for a
 * PERSISTENT component — it keeps working for everyone, even across bot restarts.
 * `arg` packs a small payload back to the handler (e.g. an item id). Use the legacy
 * `customId` only for a transient one-shot flow read by `interaction.awaitComponent()`.
 */
type Component =
  | { kind: "button"; handlerId?: string; customId?: string; arg?: string; label: string; style?: "primary" | "secondary" | "success" | "danger" }
  | { kind: "select"; handlerId?: string; customId?: string; arg?: string; placeholder?: string; options: { value: string; label: string }[] };

type ReplyPayload =
  | string
  | { content?: string; embed?: any; ephemeral?: boolean; components?: Component[] };

/** The live interaction handed to a slash-command handler. */
interface Interaction {
  options: Record<string, any>;
  guildId: string;
  channelId: string;
  userId: string;
  displayName: string;
  reply(payload: ReplyPayload): Promise<void>;
  followUp(payload: ReplyPayload): Promise<void>;
  /** Pop a modal and resolve with the submitted field values (keyed by field id). */
  modal(spec: ModalSpec): Promise<Record<string, string>>;
  /** Resolve when the user clicks a button / picks an option. */
  awaitComponent(opts?: { timeoutMs?: number }): Promise<{ customId: string; values?: string[] }>;
}

/** The interaction handed to a persistent component handler when a button/select is clicked. */
interface ComponentInteraction {
  /** The `components` key that was clicked. */
  customId: string;
  /** The small payload packed into the component's `arg`. */
  arg?: string;
  /** Selected values, for a select menu. */
  values?: string[];
  guildId: string;
  channelId: string;
  /** The message the component lives on. */
  messageId: string;
  /** The user who clicked. */
  userId: string;
  displayName: string;
  /** Reply privately to the clicker only. */
  reply(payload: ReplyPayload): Promise<void>;
  /** Edit the source message in place (live tally / attendee list). Pass only what
   *  changes; omit `components` to keep the existing buttons. */
  update(payload: { content?: string; embed?: any; components?: Component[] }): Promise<void>;
  /** Acknowledge the click with no visible change. */
  deferUpdate(): Promise<void>;
}
type ComponentHandler = (i: ComponentInteraction) => Promise<void> | void;

interface CommandDef {
  name: string;
  description: string;
  options?: OptionDef[];
  /** "manage_guild" to gate to server managers, or null for everyone. */
  defaultMemberPermissions?: "manage_guild" | null;
  guildOnly?: boolean;
  handler(interaction: Interaction): Promise<void> | void;
}

interface KbSeed { type?: "url" | "website"; uri: string; title?: string }
interface GlossarySeed { subject: string; fact: string }

interface SettingsField {
  key: string;
  type: "text" | "textarea" | "channel" | "number" | "toggle";
  label: string;
  desc?: string;
}
interface SettingsSchema { fields: SettingsField[] }

/** Context passed to onEnable (runs once when an admin turns the extension on). */
interface EnableCtx { guildId: string }

/** A member as seen by an event handler. */
interface EventMember {
  id: string;
  /** Server nickname or display name. */
  displayName: string;
  /** Discord username (without nickname). */
  username: string;
  /** A mention string (`<@id>`) to ping them. */
  mention: string;
  bot: boolean;
}

/** Context passed to a gateway-event handler (e.g. `events.memberJoin`). There is no
 * interaction to reply to — post with `host.discord.send(channelId, …)`. */
interface EventContext {
  event: string;
  guildId: string;
  /** The member the event is about (for member events like `memberJoin`). */
  member: EventMember | null;
}

type EventHandler = (ctx: EventContext) => Promise<void> | void;

interface ExtensionSpec {
  id: string;
  name: string;
  version?: string;
  description?: string;
  category?: string;
  /** A line folded into the system prompt while the extension is enabled. */
  systemNote?: string;
  defaultEnabled?: boolean;
  permissions: Permission[];
  tools?: ToolDef[];
  commands?: CommandDef[];
  seeds?: { kbSources?: KbSeed[]; glossary?: GlossarySeed[] };
  settingsSchema?: SettingsSchema;
  /**
   * Persistent button/select handlers, keyed by a short name (a-z 0-9 _). A component
   * whose `handlerId` is one of these keys keeps working for every user and survives
   * restarts; each click runs the handler with a ComponentInteraction. Pair with `arg`
   * to carry a small payload (store anything bigger in host.kv and pass its key).
   */
  components?: Record<string, ComponentHandler>;
  /**
   * Gateway-event handlers, keyed by event name (currently `memberJoin`). The host runs
   * the handler when the event fires for a guild where this extension is enabled. Event
   * hooks are first-party only — built-in and locally-authored extensions, never imported
   * or marketplace code. Post with `host.discord.send(channelId, …)`; there's no reply.
   */
  events?: { memberJoin?: EventHandler } & Record<string, EventHandler>;
  /** Runs once on the OFF -> ON transition; use it to seed durable state. */
  onEnable?(ctx: EnableCtx): Promise<void> | void;
}

/** Register your extension. Call exactly once at the top level. */
declare function defineExtension(spec: ExtensionSpec): void;

interface FetchResponse {
  status: number;
  ok: boolean;
  headers: Record<string, string>;
  text(): Promise<string>;
  json(): Promise<any>;
}

/** Host capabilities. A method only works if you requested its permission. */
declare const host: {
  /** Call an external HTTP(S) API. Private/loopback hosts are blocked. */
  fetch(url: string, init?: { method?: string; headers?: Record<string, string>; body?: string }): Promise<FetchResponse>;
  kb: { addSource(seed: KbSeed): Promise<boolean> };
  glossary: { add(fact: GlossarySeed): Promise<number> };
  kv: {
    get(key: string): Promise<any>;
    set(key: string, value: any): Promise<void>;
    delete(key: string): Promise<void>;
  };
  /**
   * Read this extension's per-guild settings — whatever an admin entered in the
   * settings pane you declared with `settingsSchema`. Read-only; no permission needed.
   * `get()` returns the whole object, `get(key)` a single value.
   */
  settings: { get(key?: string): Promise<any> };
  /** Read an operator-approved secret by reference (never the literal value at author time). */
  secret(ref: string): Promise<string | null>;
  /** Build a Discord embed to pass to interaction.reply({ embed }). */
  embed(spec: EmbedSpec): any;
  /** Write a line to the bot's log (always available). */
  log(message: string): Promise<void>;
  /**
   * Generate text in the server's persona voice. The guild persona is applied as the
   * system prompt automatically, so output stays in character. Resolves to the generated
   * string. Needs `model.generate`; uses the operator's own model quota.
   */
  generate(opts: { task: string; maxTokens?: number; systemNote?: string }): Promise<string>;
  discord: {
    /**
     * Post a message to a channel — for event handlers and tools, which have no interaction
     * to reply to. `channel` is a channel id, `<#id>` mention, or name (resolved in the
     * server). The payload may carry interactive `components` (persistent buttons/selects
     * keep working). Needs `discord.send`; rate-limited, and a third-party extension's post
     * can't @mention anyone. Resolves to a status string.
     */
    send(
      channel: string,
      payload: string | { content?: string; embed?: any; components?: Component[] },
    ): Promise<string>;
  };
};
