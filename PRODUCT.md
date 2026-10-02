# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

The marketing site is static HTML/CSS in `docs/`, served by GitHub Pages with no build step, with [Motion](https://motion.dev/) loaded as an ES module (the user's choice, 2026-09-30). The console is React + Vite + TypeScript in `web/`; the desktop app is Electron around a PyInstaller backend.

## Users

Primary: Discord server owners and admins who want an AI member in their community and are willing to run it themselves. Arriving at the landing page, they are asking what it will do in their server, and what it will cost them in money, setup, control and trust.

Secondary: members of a server who come across Olisar and want it in their own server. They can't add it themselves, because it runs as the owner's own bot, so what they need is what talking to it is like, what they control about their own data, and something to send their admin.

## Product Purpose

Olisar is a self-hosted AI Discord bot that behaves like a member of the server. It reads the channels it's allowed to, remembers context, answers from the server's own knowledge, and joins in under its own name and personality. Owners configure it from a private admin console, or by asking it in Discord. Success is an owner who installs it, finishes setup in minutes, and finds it has earned its place in the channel.

## Positioning

It runs as the owner's own Discord bot, with their name, avatar and persona, on their own computer or a free cloud server, using their own free API keys. There is no Olisar cloud and no subscription. A hosted AI bot can't say the bot and its database belong to the owner.

## Operating Context

- Installed as a desktop app: macOS 13+ on Apple Silicon, or Windows 64-bit. No Linux or Intel Mac desktop build; server hosting runs on Linux.
- Setup is a wizard: paste a bot token, a client secret and a free Gemini key. It turns on the intents the bot needs, invites the bot, and notices when it joins.
- Three places to run it, mirroring the wizard: on your computer (online while it's on); on your computer and shared over Tailscale Funnel, so co-admins sign in from anywhere; or 24/7 on a free Oracle Cloud Arm server, which the app installs over SSH with no terminal. Oracle asks for a card to verify identity, its free Arm servers are often out of capacity, and this path needs a Tailscale account.
- One install runs several bots at once, each in its own process with its own data, keys and sign-in.
- Members reach it by saying its name, @mentioning it, replying to it, DMing it, or `/ask`.
- Owners change settings in the console, or by asking the bot in Discord behind a PIN; the Activity log records who changed what and keeps the old value.

## Capabilities and Constraints

Everything below is true of 2.0. The landing page describes 2.0 and goes live when 2.0 ships as stable (as of 2026-09-30 the latest stable is 1.5.0 and 2.0 is at beta-5).

- Message search across the channels it indexes; when an answer comes from one message it pastes that message's link, which opens it in Discord. Search only returns what the person asking can open.
- Knowledge base: web pages, crawled sites, and uploaded PDF, DOCX, TXT and MD files, plus a glossary of the server's own terms.
- Memory: rolling channel summaries, recall, durable facts, and a private impression of each member.
- Ends a turn with a reaction instead of a reply when a message only needs acknowledging ("thanks", an FYI), or after doing what was asked.
- Welcome messages written for the channel the new member lands in.
- Reminders, DMs, and posting to other channels when asked.
- Looks at posted images; generates images with an optional free Cloudflare Workers AI key.
- Extensions: built-ins (dice, calculator, concise mode), a marketplace inside the console, and an SDK for writing your own. A Star Citizen pack exists; it is one example, not the lead.
- Control: every channel starts off; per channel the owner picks where it reads, where it talks, and where it only takes reference. Roles decide who can use it. Joining conversations on its own is opt-in, with cooldowns and quiet hours. @everyone pings are off until an admin turns them on.
- Free to run on Gemini's free tier. Each model has a daily limit; Olisar falls back through a ranked chain of models, and when all are spent it stops replying until midnight Pacific. The Usage page shows what's left.
- Data: each bot keeps one local SQLite database on the owner's computer or server. Anything Olisar replies to is sent to Google Gemini. On the free tier Google may use that content to improve its products and human reviewers may read it; the EEA, UK and Switzerland are exempt.
- Members control their own data: `/privacy` shows what's kept, `/forget-me` removes a person entirely, and the member portal lets them see and delete single facts. The all-channel search index is an admin's explicit choice and is disclosed by `/privacy`.

## Brand Commitments

- Name: Olisar. Two marks: the brand mark is a violet squircle holding a glowing pale orb (`docs/logo.png`, sources in `icons/Icons Logo/` and `icons/ico_brand.icon/`), used on brand surfaces like the website and README; the dashboard's own mark is a slate shield with a navy star (`web/public/logo.png`, the desktop dock and tray icons). Don't recolor either.
- Voice: second person, plainspoken, lightly opinionated; calm, competent, a little dry. Sentence case everywhere, US spelling, no emoji in the interface. Full rules in `web/DESIGN.md` under Brand & voice.
- The landing page uses the console's design system (`web/DESIGN.md`), by the user's direction. The previous landing page is not a reference.
- The landing page's example conversation shows the bot as Olisar; its profile figure cycles through names and avatars an owner might give their bot, because each install runs as the owner's own bot.

## Evidence on Hand

- Logo PNGs in `docs/` and `web/public/`.
- Cedro Basic 3D glass icons, supplied for the landing page: `/Users/gabriel/agents/icons/cedro-basic/{dark,light}/`, twelve 1600px PNGs (cloud, fire, heart, lightbulb, lightning, mail, moon, play, shield, star, thumb-up, user).
- Console screenshots in `docs/mockup-*` predate 2.0's redesign and are out of date.
- No install counts, testimonials, customer logos or press exist. Don't invent any; a live GitHub star count is the one honest number available.
- Demo conversations are authored. Label them as examples wherever a visitor could take them for a real server.

## Product Principles

1. Show it working. Owners judge a bot by what it does in a channel, so demonstrate behavior instead of listing features.
2. Say the limits before the owner hits them: the daily cap, what Google sees, and what free cloud hosting asks for.
3. It's the owner's bot: their name, their machine, their keys, their database.
4. Quiet by default. Channels start off, joining in is opt-in, and a reaction beats a reply written to have replied.
5. Members keep control of what's known about them, and the page tells them how.

## Accessibility & Inclusion

WCAG AA: body text at 4.5:1 against the lightest ground it sits on, visible focus on every control, and `prefers-reduced-motion` honored by slowing motion rather than deleting it (per `web/DESIGN.md`).
