## [Unreleased]

Ask Olisar to DM someone and two things happened: the DM went out, and then it wrote "done". Every path through the reply pipeline ended in text — if the model produced none, the pipeline forced an answer out of it, and failing that the user got the blank fallback and a Report button. Its own style notes have asked it to react instead of replying since 1.4.4, and it could not.

Now it can. Olisar reacts to the message and stops there, after doing what was asked or when a message only needed acknowledging at all. Going quiet is the failure this risks — from the channel, a bot that decided to say nothing and one that crashed look the same — so silence is refused unless it has been earned: the reaction has to have landed, there has to be a message to react to, and a turn that looked something up still owes what it found.

Changing anything about Olisar also meant opening the console. You can now ask it in Discord instead: its persona and system prompt, how and when it joins in, its command replies, its knowledge sources, the search index and glossary, and a member's impression. The tools that make those changes are only handed to the model once a conversation turns to settings, so every other reply costs about what it did before.

Olisar could find a past message but couldn't point to it. Search came back with a link to every hit, and Olisar was told to keep it to itself unless someone asked where something was posted. Now when an answer comes from one message, the link comes with it, and clicking it opens the message.

Linking as a matter of course meant fixing what search had been doing all along. It read every channel Olisar could, whatever the person asking could see, so a member asking about something posted in a staff channel was told what was said, who said it and when. The older messages Olisar recalls weren't even limited to the server. Both now stop at what the asker can open.

Every release went to every install at once, and four since 1.0 needed a fix the same day. There's now a beta channel: early builds of the next release go to the installs that opt into them, and a release reaches everyone else once it's done. Versions change shape with it. From 2.0 on a stable release has two numbers, 2.0, 2.1, and the betas leading up to one count up to it: 2.0.beta-1, 2.0.beta-2, then 2.0.

The console itself was cards inside cards. Every group of settings sat in a bordered box, and every input in it was a bordered box too, so a page was mostly edges. Groups are now a heading and a thin rule, with each setting on its own row, and the controls are the only things with a border.

When something went wrong, the way to tell the Olisar team was two clicks behind a gear, and it opened blank. The screens where people actually get stuck now link to it, and the form arrives filled in with what just happened.

An install could hold several bots, but only one of them was ever online: switching stopped the bot you were on and started the other, and the switcher itself had been taken out of Settings. Keeping two bots online meant putting one on a cloud server, and a second bot on a server meant creating and setting up a second server.

Every bot on the desktop app now runs at once, each in its own process with its own data, keys and sign-in, so one bot can't read another's settings, take another down when it crashes, or slow it down. The console still shows one bot at a time, and switching only changes which one. A server can host several bots too: putting a bot on a server another of your bots already uses needs nothing new from you, and each gets its own install there.

The console had grown a description under nearly every setting, and many of them restated the label above them. Someone went through every page marking what to cut, and most of this release's console changes are that: shorter copy, and a few controls that said the same thing twice. Two of the proposed lines would have been wrong, so they say what Olisar actually does instead. The docs got the same pass: they were set like a settings page, small and tight, and now read like documentation.

Setup showed four steps whichever way you chose to host Olisar, so two of the three choices fit their own setup into a step shaped for another. Each choice now has the steps it needs.

Updating could leave the console on the version before it. The page was kept by the browser and reused without asking the server, so after 2.0.beta-2 the desktop window ran the old console against the new backend. The console's page is now checked with the server every time it loads.

Olisar could answer the same message twice. A question asked by name that took more than about 15 seconds to answer still looked unanswered to the part of Olisar that joins conversations on its own, so a second reply could land under the first. That same part never counted its replies toward the hourly limit, and never checked whether the person was someone Olisar is set to ignore.

Setting Olisar up took eight fields and three trips to the Discord Developer Portal, and the wizard never mentioned one of them: inviting the bot. You'd finish setup, sign in, and be told Olisar wasn't in any server, with no link to fix that. Most of what the portal was visited for can be read from the bot token, so setup now takes the client ID from it, turns the intents on through it, and builds the invite link from it. What's left to paste is the token, the client secret and a Gemini key, and each step ticks itself off once it's done.

Finishing setup didn't mean Olisar worked. Every channel starts off, so a server it had just joined heard nothing from it and nothing said why. A bot Discord refused over an intent looked the same as one switched off, and landed its operator on a screen saying it wasn't in any server. A saved key showed as set whether or not it was right. The console now says what's left and what's wrong, and where it can, fixes it.

### New

[f3e8f10] — A manual workflow, Point :latest at a release, puts the server image's `latest` tag back on a stable release without rebuilding it.

[c6d7bd8] — Settings → Updates picks a channel: Stable gets finished releases, Beta gets early builds of the next one and every stable release as it ships.

[c6d7bd8] — Switching from Beta to Stable keeps the beta you're on until a newer stable release is out, instead of taking you back to an older version.

[c6d7bd8] — Installing a beta by hand puts the app on the Beta channel.

[c6d7bd8] — A server-hosted bot's VM follows the app's channel, and the app never moves it to an older release than the one it runs.

[a3933dc] — When an answer comes from one specific past message, found by search or remembered, Olisar pastes that message's link, which opens it in Discord.

[a3933dc] — A message link Olisar wasn't actually given is removed from the reply before it's sent, so a mistyped or made-up link never goes out.

[a3933dc] — The test harness has a scenario for it: an answer that lives in another channel should come back with the link, and the small talk after it without one.

[d0311f6] — Olisar can read and change its own settings when asked in Discord, covering everything on the Persona, Behavior and Command replies pages.

[d0311f6] — It can add, re-read, reschedule and remove knowledge sources, rebuild or clear the message search index, mine the glossary or delete entries from it, and rebuild a member's impression.

[d0311f6] — A change made from chat shows in the Activity log under the member who asked, with the previous value kept.

[d0311f6] — Until the tool PIN covers them, anyone who can talk to Olisar can make these changes; `OLISAR_PIN_GATED_TOOLS=change_setting,settings_action` puts them behind it now.

[f2fa419] — Olisar can end a turn with a reaction and no message, after sending a DM, posting to another channel, remembering something, or setting a reminder.

[f2fa419] — A message that only needs acknowledging — "thanks", an FYI — gets a reaction rather than a reply written to have replied.

[f2fa419] — Settings → Behavior → Model & tools turns it off per server; off, every turn ends in words.

[f2fa419] — A turn answered with a reaction is recorded, so the next reply doesn't read it as having been ignored and do the thing twice.

[1e2c722] — The test harness observes reactions, with six scenarios covering the silent turns and the question that must still get an answer.

[ae7cbcb] — A failed server deploy in setup offers to send the error and the end of the install log to the Olisar team.

[ae7cbcb] — Connecting or reconnecting to an existing server, and the last step of setup, offer a report when they fail, and a Tailscale failure offers to ask the team.

[ae7cbcb] — The access-denied, no-servers and suspended screens can reach the Olisar team; the suspended screen said to contact them and gave no way to.

[a8109fb] — Someone refused at sign-in can send that message from a remote console too, for an hour after the refusal, and it never carries the install's logs.

[ae7cbcb] — A page that crashes, or fails to load again after Try again, has a Report button that arrives with the page and the error written in.

[ae7cbcb] — Settings → Logs sends what it shows with a bug report.

[ae7cbcb] — Test chat can report a reply that wasn't right, with what you said and what it answered.

[ae7cbcb] — A failed or rolled-back server update stays on the server panel, with a link to report it, instead of appearing as a toast when the panel opens.

[ae7cbcb] — Every docs page ends with a way to ask the team about it.

[ae7cbcb] — The command palette finds Feedback when you type "report a bug", "contact the team" or "help".

[04700db] — Every bot on the desktop app runs at the same time, each in its own process; switching bots in the console changes which one it shows and never stops one.

[04700db] — With two or more bots, the top of the sidebar shows the bot on screen and whether it's online, with a menu to switch or add one; the sign-in, setup and server screens carry the same menu in their top-left corner.

[04700db] — Settings → Bots is back: open, rename, move, reset or delete any bot, and pick which one opens on launch.

[04700db] — A bot that can't start says so with the end of its output and a Retry, and the others keep running.

[04700db] — Setting up or moving a bot onto a server offers the servers your other bots already run on, with no new VM, SSH key or Tailscale key to set up.

[04700db] — Reconnecting to a server that runs several bots asks which one this is.

[6804316] — Knowledge's source, glossary and activity lists stop after a few rows and scroll, with a fade at whichever edge has more past it.

[6804316] — The member portal's remote-access warning links straight to Settings → Remote access.

[22bbe45] — Setup turns on the Message Content and Server Members intents itself, which Discord allows for any bot in fewer than 100 servers.

[fd49f6d] — When Discord won't let setup turn an intent on, setup links to the switch on the Bot page and waits until it's on.

[22bbe45] — Setup makes the Add App button on the bot's Discord profile add the bot, when it's still on Discord's default of adding only the bot's commands.

[fd49f6d] — Setup has an Add to Discord step with the bot's invite link, which asks for only the seven permissions Olisar uses, and moves on once the bot has joined a server.

[fd49f6d] — The client secret and Gemini key are checked as they're pasted.

[fd49f6d] — Setup ticks off each redirect URL once it's registered in the Developer Portal, and links straight to that app's OAuth2 page.

[d21c6b4] — The server switcher can add Olisar to another server, or copy the invite link for whoever manages it.

[d21c6b4] — "No servers yet" has a button that adds Olisar to a server, and opens the console once the bot joins.

[d26bc50] — A Get started list under the server switcher shows what a server still needs, a channel to reply in and the Gemini key, and ticks each off once it's done.

[d26bc50] — The Channels page warns while Olisar replies in no channel.

[63ffd71] — When Discord refuses the bot, the console names the intents that are off instead of calling the bot offline.

[63ffd71] — Turn on and reconnect, on that screen or the sidebar's bot card, switches the intents on where Discord allows and restarts the bot.

[021e6ae] — A server-hosted bot's control panel shows the redirect URL its console needs, and ticks it off once it's registered.

[021e6ae] — The control panel says when Discord refuses the server's bot over an intent, and can turn it on and restart the bot.

[31d8676] — The API keys page checks each key with its service and says whether it works, whether it's the one you typed or the saved one.

[31d8676] — A Cloudflare token that can see its own account fills in the account ID.

### Changed

[c6d7bd8] — Stable versions have two numbers from 2.0 on, and a beta is numbered after the release it leads up to, as in 2.0.beta-1.

[c6d7bd8] — Betas are published as GitHub pre-releases, and the server image's `latest` tag only moves for a stable release.

[c6d7bd8] — The Windows installer is uploaded with `gh`, like the macOS one, rather than by electron-builder.

[a3e92ef] — The app applies a release to your server itself whenever it starts up on a newer version than the VM, which is every launch after it updates itself.

[a3e92ef] — Reconnecting to a VM, or switching to a server-hosted bot, brings that server up to this build too.

[a3e92ef] — The control panel reports an update it didn't start: **Updating…** while it runs, and the outcome when it lands, instead of reading the restarting container as a server that fell over.

[a3e92ef] — The VM's daily update timer is gone, and its systemd units are removed from servers that still have them on the next connect, deploy, or re-bootstrap.

[ed004dd] — Console pages group their settings under a heading and a rule instead of in cards, with each setting's name and description on the left and its control on the right.

[ed004dd] — A switch no longer repeats its setting in a caption beside it, and the two switches on Behavior that were both called "Enabled" are now "Speak up on its own" and "React with emoji".

[ed004dd] — Members and the extension marketplace are lists instead of grids of cards.

[ed004dd] — Usage shows today's four figures in one strip and its charts in panes split by a rule.

[ed004dd] — Behavior puts Proactivity and Passive reactions straight after Engagement, and Persona keeps style notes and the bio in one section.

[ed004dd] — Command replies splits slash commands from the replies Olisar sends on its own, and shows each reply beside its Discord preview.

[ed004dd] — API keys lists Cloudflare before UEX.

[04700db] — Each bot keeps its own console sign-in, so switching back doesn't ask you to log in again; bots other than the first are signed out once by this update.

[04700db] — A bot's uploads, Tailscale device and logs live in its own folder; the first bot's stay where they were.

[04700db] — Quitting the app waits for every bot to sign out of Discord, and an update on Windows waits for them before it installs.

[04700db] — Updates of different bots on one server take turns instead of running at once.

[04700db] — If more than one bot had remote access on, all but one get a Tailscale device of their own, and a new web address with it.

[6804316] — Docs has a larger body, more space between paragraphs, headings and lists, and no "On this page" index; the article takes its width.

[6804316] — Server type's unset option is called Automatic.

[6804316] — Eagerness no longer offers "off", since the Speak up on its own switch is the off; a server that saved "off" shows as switched off.

[6804316] — Number fields no longer carry a Reset button; each still shows its range and default underneath.

[6804316] — A knowledge source shows its status beside its name, and only when it isn't Ready.

[6804316] — Settings → Bots shows a bot's status only when it isn't online, and drops the Default chip that repeated the Open on launch star.

[6804316] — Command replies' text boxes are at least as tall as the Discord preview beside them.

[6804316] — Channels' Set all and Index all pickers line up with the columns they set.

[6804316] — The sidebar's server picker is as wide as everything else in the sidebar.

[6804316] — Usage calls the daily model self-test Health checks instead of canary.

[6804316] — Descriptions on Persona, Behavior, Command replies, Access and Knowledge are shorter, and several that restated their label are gone.

[6804316] — Name triggers say they aren't case sensitive, and Web search says Google's free search quota can run out for the day.

[9da31c7] — Setting up Local shared hosting has its own Remote access step for Tailscale, which lists both redirect URLs to add once remote access is on.

[9da31c7] — Setting up Server hosting keeps the API keys step ahead of Deploy, and a server can't continue past it without a Gemini key.

[9da31c7] — A bot token Discord rejects says so beside Test token, where a working one says who it's connected as.

[9da31c7] — Settings → Bots keeps a bot's name to one line, and shows where a server-hosted bot runs when you hover its name.

[9da31c7] — The server control panel only mentions its version when an update is available.

[fd49f6d] — Setup asks where Olisar runs first, since that decides which steps follow.

[fd49f6d] — Setup no longer asks for the client ID or the main server's ID: the ID comes from the bot token, and the main server is the one the bot joins.

[fd49f6d] — The bot token is checked as it's pasted, instead of with a Test token button.

[fd49f6d] — The Cloudflare and UEX keys are added from the console's API keys page instead of during setup.

[fd49f6d] — Server hosting's deploy step no longer asks for an admin username; the owner of the bot's Discord app is already its operator.

[31d8676] — The Cloudflare token help links to Cloudflare's Workers AI page, which creates a correctly scoped token and shows the account ID beside it.

[31d8676] — The UEX token is set on the Star Citizen extension's page instead of the API keys page.

[63ffd71] — The bot switcher says Can't connect for a bot Discord refused, instead of Offline.

### Fixed

[f3e8f10] — Publishing a beta no longer moves the server image's `latest` tag, which 2.0.beta-1 did.

[a3933dc] — Message search only returns messages from channels the person asking can open. A member asking about a staff channel used to be told what was said there, who said it and when.

[a3933dc] — The older messages and summaries Olisar recalls come from the channel it's replying in and channels the asker can open, not from any channel, another server, or someone else's DMs.

[7c9263a] — Olisar no longer hands over its own operating rules. It used to protect them only against instructions hidden inside pasted content, so anyone who asked by a route it trusted — a server policy it had been taught, someone it had saved as a maintainer, a request to file them in another channel, or a few members agreeing that refusing was strange — got them back verbatim.

[7c9263a] — A glossary entry is no longer treated as something that can grant permission. Anyone could teach Olisar a "fact" about the server, and it read back as community truth to every member afterwards; entries now read as claims people made, and can't authorize anything.

[1e2c722] — `arena doctor` accepts Grok, which it had rejected since the backend landed.

[1e2c722] — The Grok CLI no longer runs inside the repo, where it read AGENTS.md and wrote emulator lines as a coding assistant.

[1e2c722] — A model parked because Google retired it stops voiding every run made in the hour after a restart.

[c23a12e] — The guardrail suite reads what Olisar posted in other channels, not only what it replied here, so a refusal in one channel and a dump into the next stops scoring as a pass.

[a80d9bb] — Warning callouts that open a section on Access no longer sit against their top edge.

[a80d9bb] — The box shown when a page fails to load has a border and rounded corners; it referenced two tokens that don't exist.

[a80d9bb] — A link inside a sentence no longer makes its line taller than the ones around it.

[04700db] — A web page open in your browser can no longer reset a bot or run a server update by sending a request to the app on your machine.

[6804316] — The sidebar's Search button no longer squashes around its ⌘K key on a short window.

[6804316] — A placeholder inside bold or italic text in a Command replies preview shows as a placeholder, not as bold text.

[9da31c7] — The Continue button on the first setup step no longer lurches sideways when you hover it.

[9da31c7] — "Turn on remote access before continuing" goes away once remote access is on.

[9da31c7] — A UEX token entered while setting up a server now reaches the server.

[ae930be] — After an update, the console opens as the new version instead of the previous one kept in the browser's cache, in the desktop app and in a browser opening a server's console.

[b7a08db] — The desktop app clears its page cache the first time a new version opens, so updating from a version older than this one lands on the new console too.

[ee4ee53] — Setting up a server-hosted bot again on the server it already runs on applies the new settings, such as a replaced Tailscale key; before, the running bot kept its old ones.

[d15afcd] — A question asked by Olisar's name no longer gets a second, unprompted reply when the first one takes a while to write.

[d15afcd] — An unprompted reply is dropped if someone else spoke in the channel while Olisar was writing it.

[60bd29c] — The hourly limit on how often Olisar joins in unprompted now works; before, it never counted a reply.

[6eeab43] — Olisar no longer replies or reacts unprompted to people in a blocked role or on the global ban list.

[fd49f6d] — Deploying to a server with a Discord username in the admin field no longer leaves the server unable to start.

[fd49f6d] — Turning on remote access during setup and then picking a different kind of hosting turns remote access back off.

[568cc79] — The remote-access docs no longer say Olisar shows a per-device Funnel link or registers the tunnel's sign-in URL with Discord; it does neither.

[9388392] — A bot missing an intent no longer retries connecting every few seconds until Discord resets its token for too many attempts; it stops within 20 seconds and says which intent is off.

## [1.5.0] — 2026-09-21

A tool call runs the moment the model decides to make one. For most of them that is the point, but a few are worth a person's say-so first, and there was no way to ask for one. This release adds the asking: a 4-digit PIN, set in the console, that a tool call can be held against until someone types it into a Discord form.

Nothing is gated yet. No tool requires the PIN and there is no per-tool setting to turn one on, so setting a PIN today changes nothing anyone will see in Discord. What ships is the mechanism, proven end to end against a live server, so the policy that decides which calls need confirming can land on something that already works.

### New

[8c28371] — Settings → Security sets and changes one 4-digit PIN for the whole install, and how long a prompt waits before it lapses.

[8c28371] — A tool call can be held until someone confirms it, with the digits typed into a Discord form rather than posted as a message anyone can read back.

[8c28371] — The prompt holds the reply and drops the typing indicator while it waits, and disappears once it is answered.

[8c28371] — A prompt that lapses, is cancelled, or takes three wrong entries comes back to Olisar as a refused tool call: it says plainly that it couldn't do that part and answers with the rest.

[8c28371] — Anyone who knows the PIN can answer a prompt, so an operator can hand it to whoever should be able to approve.

[8c28371] — Every prompt writes an audit row carrying who answered it and how many tries it took, and never the digits.

[8c28371] — The prompt's wording is editable under Command replies.

### Changed

[fea9501] — Every overlay plays a short exit instead of vanishing on the frame it closed.

[fea9501] — The copy affordance cross-fades its two glyphs in place, without the bounce it used to arrive on.

[fea9501] — Buttons press to the same scale everywhere, and the save dock is 42px tall rather than 52px.

### Fixed

[fea9501] — The toggle knob travels on `transform`, so flipping one no longer re-lays out the track on every frame.

[fea9501] — The toast stack steps aside for the save dock instead of teleporting upward the moment it appears.

[fea9501] — The test chat's button keeps its press feedback while the save dock is up.

[fea9501] — The select chevron matches the icon set's stroke weight, and a user-supplied avatar gets an edge against the near-black background.

[fea9501] — The save dock only dodges the test-chat button at the widths where the two actually overlap.

## [1.4.5] — 2026-08-26

Olisar's default style notes were twenty-nine bullets on length, punctuation, capitalization, when to use an emoji and when to stretch a word. It wrote like something working through a list, because it was. A test server where several bots talk to each other made the problem legible: the stand-in members, given four sentences of character each on a cheaper model, read more like people than Olisar did in the same channel. This release replaces the checklist with a description of who Olisar is, about a tenth the length, and lets the writing follow from that.

Measured over 20 runs per arm, the new seed scored +0.40 on helpfulness, clearing two standard errors, with smaller gains on restraint and brevity. It was the only intervention in the whole programme that beat its own noise floor.

### Changed

[0aa5c96] — The default style notes are a four-sentence character sketch rather than a twenty-nine-bullet style guide.

[0aa5c96] — A server still running any previous default seed moves to the new one on next start, and a persona with one character of the admin's own writing is left alone.

[0aa5c96] — The new seed keeps short lowercase replies, no terminal period on a one-liner and the `[[break]]` rhythm, and drops the rules about emoji, stretching words and correcting typos.

### Fixed

[5e80f14] — `[[break]]` no longer reaches Discord verbatim when an extension asks Olisar to write something and then posts it.

[30e7aed] — The dashboard's test chat is briefed on the two tools it actually has, so it stops inventing the lookups it was told to perform.

## [1.4.4] — 2026-08-14

Olisar wrote like a document and behaved like a service: one paragraph per turn, the reply arrow on every message, "typing…" held for however long the model took, and an answer for every mention of its name. Underneath that, the transcript it read was flat, so two conversations running at once looked like one.

This release reworks the parts of a reply that aren't its content, and gives Olisar enough of the room to judge when a reply is owed at all.

### New

[2cc8140] — Only when addressed, in Behavior → Engagement and on by default, stops a passing mention of Olisar's name from being read as a question for it.

[2cc8140] — Persona → The room adds a server type and a slang dial, the dial drawing only on words Olisar has seen used in your server.

[2cc8140] — The channel's name and topic reach every reply, so #help and #off-topic stop sounding alike.

[2cc8140] — Replies arrive as the one to three messages the model marked with `[[break]]`, each typed for about as long as it would take to write.

[2cc8140] — The reply arrow points at a message the channel has moved past rather than appearing on every answer, and never in DMs.

[2cc8140] — See other bots, off by default, lets other bots' posts into Olisar's context without it ever replying to one.

[2cc8140] — The proactivity threshold eases when the message answers something Olisar just said.

### Changed

[2cc8140] — The default style notes are written for how chat reads: short, lowercase, no terminal period, no closing offer of help.

[2cc8140] — 45 tests cover the cadence, addressing, transcript and proactivity changes.

### Fixed

[2cc8140] — Reactions get their own signal instead of the question detector they were gated on, and questions are never candidates at any threshold.

[2cc8140] — A stored bot message carries its sender's name, so catch-ups, summaries and recall stop attributing every bot's posts to Olisar.

[2cc8140] — History lines carry the message they were answering, so interleaved strands stop reading as one conversation.

[2cc8140] — A silence renders in the history Olisar reads, so a burst from this morning stops looking like one from ten seconds ago.

[2cc8140] — The typing indicator tracks what Olisar wrote rather than how slow the model was.

[2cc8140] — The docs no longer say name triggers match only at the start of a message.

## [1.4.3] — 2026-08-14

Three of the four things added here are about someone other than the operator getting an answer: a member seeing exactly what Olisar holds on them, a blank reply carrying its own report, and a publish that can be stopped while it runs. The fourth is knowledge sources re-reading themselves, since a page taught to Olisar last month may have changed and removing the source and adding it again was the only way to pick that up.

The fixes are what the logs and the model default had been costing. The dashboard's buffer held about two hours of heartbeats and no bot events at all, and the default model was an auto-updating alias, which is how a provider-side change arrived under a running bot and started rejecting requests.

### New

[efb923d] — A member with no Manage Server anywhere can sign in with Discord and see the impression Olisar wrote, every remembered fact with a jump-link, their recording controls, their reminders, and export or erase.

[efb923d] — Member sign-in is its own ladder, with separate tables, cookie and signing salt, so a member token fails signature verification against the admin routes.

[60e8ea8] — A blank reply carries a Report this button that lands an admin in the console and a member in their portal, with the bug report written and the logs from that moment attached.

[60e8ea8] — A report can only be opened by the person it happened to, and nothing about the failure travels in the URL.

[77ac186] — Every web knowledge source carries a re-read interval, from hourly to monthly, and due sources flow back through the ordinary ingest worker.

[20fa4e9] — A marketplace publish runs behind a toast carrying Stop rather than a modal that can't be dismissed, and nothing is pushed if the operator goes away.

### Changed

[20fa4e9] — `toast()` takes an options bag of `sticky`, `busy` and `action` and returns a dismiss handle, with the recipe added to DESIGN.md.

[20fa4e9] — `req()` accepts a caller-supplied cancel signal and tells a timeout apart from a cancel, so only the timeout gets a message.

[efb923d] — The settings modal was trimmed.

[60e8ea8] — `/forget-me` and the member export both cover the failure-report table, whose rows expire after seven days.

### Fixed

[5436a26] — The dashboard log buffer drops heartbeats on the way in and keeps 4xx and 5xx access lines and SSH warnings, turning roughly 100 minutes of history into weeks.

[4dc9f11] — The default model is pinned instead of resolving to whatever Google ships today, and installs still on the alias are migrated.

[4dc9f11] — A capability 400 or a 404 parks that model and tries the next one instead of taking the request down.

[77ac186] — Ingest checks that a read produced something before deleting the chunks it was replacing.

[60e8ea8] — An exhausted quota reads as a rate limit, which waiting fixes, rather than as the generic blank.

## [1.4.2] — 2026-08-10

Every tool-backed reply was coming back as "…my mind just went blank there." The tool itself ran fine; what failed was handing its result back to the model. The function-response turn carried `role="tool"`, which the SDK has never documented as valid, and Gemini 2.x simply tolerated it. Once the default `gemini-flash-latest` alias rolled onto a 3.x model, the stricter check started rejecting the request with `400 INVALID_ARGUMENT`. A 400 isn't transient, so the model chain correctly refused to fall back and the reply became the blank fallback.

It read as intermittent rather than broken because plain replies were unaffected. On one server, both tool calls in 40 hours of logs failed: the same question, asked twice, ten hours apart.

### Fixed

[db21d8c] — Function results go back to the model as a `user` turn, which the API accepts.

[db21d8c] — A regression test asserts the function-response turn uses a role the API accepts.

## [1.4.1] — 2026-08-09

Seven routes through the console discarded unsaved work without asking, and the guard meant to prevent exactly that had been training operators to click Discard by firing on edits identical to what was already saved. Underneath sat a worse one: a settings form that enabled its save bar before its data had arrived, so a failed load rendered a form of blanks and Save wrote that over the extension's real configuration.

The rest is the design and accessibility pass that found them, plus the macOS release machinery. Signing credentials used to be untestable except by cutting a release; they can now be proven in about a minute.

### New

[fb1f678] — A preflight workflow checks the macOS certificate and notarization credentials in about a minute, and release builds run it first.

[990bfe5] — The preflight reports on the notarization credentials even when no certificate is configured.

[75ee622] — A release build can be rehearsed end to end: it signs, notarizes and staples a real `.dmg`, publishes nothing, and hands it back as an artifact.

[7ab7ec1] — Access gains a bulk setter that acts on exactly the rows the filter is showing and fires a receipt naming the count.

[a129e48] — The access policy slider names who a setting would lock out, where the decision is made.

[d1a5d5f] — A command palette searches the console, and links are real links.

[7e173ed] — The browser's own back and forward buttons work across views.

[d92bb7f] — The server switcher takes arrow keys, opening on the server you're in.

[cee52ed] — The extension editor saves from the keyboard.

### Changed

[c61be33] — One primary action per view, one type scale, and a save that says which half failed.

[78bb341] — The danger zone is set apart and quietened.

[95ac5c8] — The configuration pages look like what they configure.

[efcb934] — The console's accessibility floor, responsive shell and runtime cost were rebuilt.

[420b9e5] — The documentation describes the console that shipped.

[05df98e] — Each documentation section has its own URL, a focus ring, and something to say when a search matches nothing.

[00b40bf] — The landing page was reworked across accessibility, type, motion and delivery.

[7ab7ec1] — The three reaction fields are named for what they do instead of borrowing their card's title.

[547a6ae] — An empty `MACOS_CERTIFICATE` builds unsigned instead of failing the release.

[a6b2f91] — electron-builder no longer publishes the macOS build out from under `gh`.

### Fixed

[02dfbe5] — Changing tab or server asks before discarding a page's unsaved edits, rather than destroying the draft along with the page.

[0084e48] — An extension's schema-driven settings form joins the dirty registry, so the values typed into it are guarded like the toggle above it.

[0084e48] — An unsent bug report or feedback message survives a stray Escape or backdrop click.

[53122f7] — Opening the marketplace or the editor no longer discards pending extension toggles and the bar that said they existed.

[53122f7] — A failed settings load can no longer let Save overwrite the stored row with an empty object.

[53122f7] — `beforeunload` consults every dirty registry, so closing the window over an unsent report asks first.

[7ab7ec1] — The Developer risk threshold keeps its edit across a tab switch.

[9cd0f78] — Removing a saved API key asks first, names the key, and says the value can't be recovered.

[6f7b76a] — The Usage chart no longer draws a daily request cap this system has never had.

[6f7b76a] — Behavior stops reporting unsaved changes after a trigger edit that retyped the identical value.

[f887b43] — Figures that are stale say they're the last reported rather than the current ones.

[f887b43] — The Activity ledger names all twenty-four recorded actions and resolves its who column instead of printing raw Discord snowflakes.

[1d25c55] — Undo no longer saves the value it was undoing.

[791d4aa] — "Saved" no longer appears over a form that is still dirty.

[d92bb7f] — Quiet hours echo the UTC conversion in the reader's own timezone.

[d92bb7f] — A numeric field refuses an out-of-range value outright instead of letting it into the draft.

[0084e48] — `Num` enforces the range it prints, and the API carries the same bounds so nothing else can write past them.

[f7e1e51] — A `color-mix()` against an unset variable had been silently deleting a border.

[cee52ed] — An out-of-range number in one card no longer disables saving in every other card.

[839c460] — The page that crashed every time it was opened doesn't.

## [1.4.0] — 2026-08-07

The macOS build is signed and notarized from this release on, so the `.dmg` opens without a Gatekeeper detour and the bundle the updater swaps in carries its own valid signature. Server-mode updates got the same treatment from the other side: pinned by image digest, health-checked before the VM commits to one, rolled back automatically when a new version doesn't come up.

The user-facing copy was rewritten across the console, the in-app and published docs, the landing page and Olisar's own Discord replies. Six content bugs turned up while doing it, including a Markdown bug that printed raw link syntax on the public docs site.

### New

[cbc23d5] — The macOS `.dmg` carries a Developer ID signature and a stapled Apple notarization ticket.

[b98b42d] — Server-mode releases are pinned by image digest, health-gated before the VM commits to them, and rolled back when one fails to come up.

[1897047] — Opening the server control panel pulls the current VM image.

### Changed

[90aaa64] — The user-facing copy was rewritten across the console, the docs, the landing page and Olisar's Discord replies.

[90aaa64] — The landing page hero is a real screenshot of the console.

[bc9e6b6] — CI builds the server image once per batch of merges instead of once per merge.

### Fixed

[84c054d] — DM recall is scoped to the conversation it belongs to, so a 1:1 with Olisar can't surface in someone else's.

[84c054d] — The failure path no longer dumps raw tool output into a reply.

[85ad7e3] — Grounded web search logs the reason for a refusal, backs off, and falls through the model chain instead of dying.

## [1.3.1] — 2026-07-15

The remote server control panel called a healthy VM unreachable. Status ran several SSH `docker compose` commands and scanned the entire container log history looking for the Funnel URL, which on a long-running VM routinely blew past the panel's fetch budget. It showed Unreachable and asked you to reconnect while SSH worked, Logs loaded, and the bot and Funnel were fine.

### Fixed

[2b758d7] — Status is a single bounded SSH probe that reads recent logs first and only walks the full history until the first `*.ts.net` match.

[2b758d7] — A failed status check surfaces the real error instead of a blank Unreachable badge.

[2b758d7] — The control panel's status fetch timeout is raised, so a slow SSH link has headroom.

## [1.3.0] — 2026-07-15

Extensions couldn't take a file. A slash command had no way to declare an attachment, and nothing in the SDK could move bytes between Discord, an external API and a reply without pushing them through the sandbox, which is the one place large binaries shouldn't go.

### New

[c4b9c25] — Slash commands can declare `type: "attachment"` options, with the metadata landing on `i.options.<name>`.

[c4b9c25] — `host.files.read` returns base64 up to about 20 MB and `host.files.ingest` returns a host-held `blobId` up to about 25 MB.

[c4b9c25] — `host.fetch` accepts `bodyBlobId` and `responseBlob: true`, so a compress-and-return flow never puts a large binary through the sandbox.

[c4b9c25] — `i.reply`, `i.followUp` and `host.discord.send` accept `files` carrying `text`, `contentB64` or `blobId`, up to 10 attachments and about 25 MB.

[c4b9c25] — A first `reply` marked `ephemeral: true` keeps later `followUp`s private unless one says otherwise.

### Changed

[c4b9c25] — The command sandbox has more memory headroom for base64-heavy handlers.

[c4b9c25] — The slash-flow docs and the SDK host table cover attachments, blobs and the compress-style pipeline.

[c4b9c25] — `tests/test_sdk_files.py` covers the caps, the blobs, a mocked fetch and ephemeral inheritance.

## [1.2.3] — 2026-07-08

Two things that were loud in different ways. When Olisar couldn't write a clean summary, its fallback pasted the raw message-search batches into the channel, header and all, including the model-only "skim these and answer the question" instruction that was never meant for anyone to read. And the embedding model, which powers semantic memory and search and unlike replies has no fallback, wasn't being parked on a 429: every message and every background index tick re-hit the exhausted quota.

### Changed

[68b95fa] — The body typeface is IBM Plex Sans.

### Fixed

[7292b71] — The message-search instruction header is stripped from every batch before the fallback posts it.

[f1acaac] — A rate-limited embedding model is parked briefly, and recall degrades to non-semantic context while it is.

## [1.2.2] — 2026-07-04

In server-hosting mode every control on the panel was blocked once the bot was configured. Status, the SSH key, start and stop, reconnect: the panel showed Unreachable, the key field span on "generating…" forever, and reinstalling didn't help.

Alongside that fix, a bot can now move between hosts. Olisar carries its data across itself and keeps the old copy as a backup, which makes moving a bot reversible rather than a one-way decision.

### New

[2bd80f1] — Move / change hosting moves the active bot between this computer and a cloud server, carrying persona, memory, knowledge and uploaded docs and keeping the old copy as a backup.

[1691df3] — Reset configuration clears a bot's Discord credentials, API keys and hosting config while keeping everything it has learned.

[1691df3] — Reconnecting to a server needs only its IP, since the app reuses the SSH key it already holds.

[1691df3] — The server control panel gains a settings gear with a Logs view covering the bot, the Tailscale Funnel and the app itself.

### Changed

[c8908e3] — The raw log dump under the control panel's buttons is gone in favor of the gear.

### Fixed

[1691df3] — Every control on the server panel works after setup, with real timeouts and a Retry in place of an endless spinner.

[1691df3] — Open console finds the public `…ts.net` URL even after it has scrolled out of the recent logs.

## [1.2.1] — 2026-07-04

Discord sign-in on the desktop app navigated the app's own chromeless window to Discord. Discord could reject that request with a raw "Invalid Form Body" and strand you there with no way back.

### Changed

[0e9e148] — The post-sign-in confirmation page is a branded Olisar card, in both its success and access-denied states.

### Fixed

[0e9e148] — Discord sign-in opens in the system browser, keeps Cancel and Reopen browser on the sign-in screen, and signs the app in once you authorize.

## [1.2.0] — 2026-07-04

One app, one bot, one machine was two assumptions too many. Olisar now runs several bots from one install, each its own Discord application with its own token, persona, settings, memory and database, and it can run on a free cloud VM instead of the machine in front of you, set up and controlled from the same console.

Memory upkeep follows the bot rather than its home server: channel summaries, member personas, the glossary and `/forget-me` now cover every server a bot is in.

### New

[192b5ef] — One app runs several bots, each with its own token, persona, settings, memory and database.

[dcab660] — A bot can be renamed and set as the default that opens on launch.

[192b5ef] — Background memory upkeep and `/forget-me` cover every server the bot is in.

[192b5ef] — A gear on the sign-in and setup screens opens Appearance, Bot, Updates, Desktop app and Feedback before you sign in.

[56289ae] — Onboarding offers three hosting options, including an SSH-driven deploy to a cloud VM with an app-generated key.

[5f5f9dc] — Olisar can connect to a VM that already runs it, and reconnect after a changed key or IP.

### Changed

[d2f8b94] — The landing page has a subtler grid-lit glow, a copyright line and consistent spacing.

[dcab660] — The docs cover running multiple bots and hosting one on a server.

## [1.0.5] — 2026-07-02

Olisar could read a still image and not a GIF, which is a good share of what actually gets posted. Uploads and Giphy or Tenor links are both read through their first frame now, and the model is told that's what it's getting rather than being left to guess.

The rest is reach: a tool for relaying a message to a channel you name loosely, direct messages that are indexed like anywhere else with a per-user opt-out, and a Usage page rebuilt around the limits that exist.

### New

[357818d] — Uploaded GIFs are read as images via their first frame, with the model told it's a GIF and seeing only that frame.

[8b6ae2b] — Giphy and Tenor links from Discord's GIF picker are read the same way.

[1889a36] — The `send_to_channel` tool relays a message to a named channel on request.

[5974bb6] — Channel resolution uses fuzzy matching and an in-context channel directory, so the exact name or ID isn't needed.

[064f888] — Direct messages are stored and indexed as their own guild-0 channels, with a per-user opt-out through `/dm-indexing`.

[8f452e5] — Usage tracks per-model and per-process Gemini usage and live requests per minute.

[426e72b] — The Usage and rate-limits page was redesigned, with more chart headroom.

### Changed

[824b156] — Hover tooltips are kept to icon-only buttons.

[d586430] — The glossary shows a fact's full text on hover.

[312f2d7] — The model's tools note matches the current tool set.

[caac6f7] — The operating rules are looser, leaving more room for judgment.

### Fixed

[21a4d45] — The glossary stops duplicating and rephrasing facts it already holds.

[55aae25] — Tiny slices on the Usage donut get a minimum arc, so they stop overlapping.

## [1.0.4] — 2026-07-01

1.0.3 shipped a regression that crash-looped the backend with "could not bind on any address" on cloud and headless deployments, and on the desktop whenever remote access was on. The funnel hostname was being used as the address the server binds to.

### Fixed

[c7a1dbc] — The funnel hostname no longer shadows the bind address.

## [1.0.3] — 2026-07-01

Knowledge sources were scoped wrong across guilds. A source added from the console sat on Pending forever, and one added from Discord with `/olisar learn-url` never appeared in that server's console at all.

The rest of the release is control over what Olisar keeps in working context, per server, and a way to build the glossary on demand instead of waiting for it to happen.

### New

[91a456c] — Behavior → Memory & summaries gains a per-server context window setting for how many recent messages Olisar keeps.

[91a456c] — Knowledge → Glossary gains Mine from memory and Deep mine from index.

[96be602] — The backend can run on a server rather than a desktop, over Docker and Tailscale Funnel.

[cf866bf] — Clear memory moves into Settings → Bot, behind a typed confirmation.

### Changed

[0dbdffa] — The docs' Behavior section was rewritten around a Memory & summaries subsection.

[fb2c4fa] — Every destructive confirm in the console is gated behind the type-to-confirm field.

### Fixed

[f350253] — Knowledge sources are scoped per guild, so console-added sources leave Pending and Discord-added ones appear in the right console.

[4dd4ce6] — On a headless deployment the Remote access panel and the sidebar show the live public URL instead of sitting on "Starting…".

## [1.0.2] — 2026-06-28

The installed app couldn't read its own version number, so Settings → Updates always displayed v0.0.0 and offered an update that never seemed to apply, including right after updating.

### Fixed

[7974012] — The frozen backend reports its real version instead of 0.0.0.

## [1.0.1] — 2026-06-28

On a fresh Windows install the app opened to a gray screen and then "the backend didn't start in time." A status symbol the backend logs couldn't be encoded by Windows' legacy console code page, which took the whole process down before the dashboard could load. macOS was unaffected.

### Fixed

[97dd12d] — Backend I/O is forced to UTF-8, so a Windows code page can't crash startup.

## [1.0.0] — 2026-06-28

Olisar 1.0: a self-hosted AI companion for Discord that runs as one desktop app on a machine you own. It brings its own Discord bot and your own free Gemini key, and everything it knows stays in a local database. No server to rent, no config files.

What 1.0 adds on top of the 0.4 line is mostly trust and control. The marketplace publishes behind a review rather than on faith, the console was rebuilt against its own design system, and the things that used to need a config file or a Discord command now have a place in Settings.

### New

[b299a6f] — The marketplace publishes behind an AI risk review, with guardrails, reports and a developer console.

[de3cf66] — Publishing shows the review in the console as a scan to verdict flow.

[63d0a33] — Settings carries an on/off toggle for the remote-access funnel.

[4c3bc10] — Settings gains a Feedback page that emails the operator.

[0bc611e] — Olisar is subtly aware of the message you replied to, in channels and in DMs.

[6aa99ad] — Olisar responds in DMs, and stays aware of them even when its target guild is stale.

[8e4e951] — Behavior can bar the bot from pinging `@everyone`, `@here` or roles.

[753a544] — The SDK gains event hooks, `host.generate` and `host.discord.send`, and Welcome becomes a pure SDK extension.

[9cb28db] — A trusted extension tool can post to a channel through `host.discord.send`.

[e773138] — SDK buttons and selects survive a restart.

[ad9b2ac] — The marketplace gains push-update and yank-to-detach, and recovers a rotated publisher token on its own.

[ad63eed] — The Discord application's owner is always an operator.

[b24d792] — Six community extensions are published under the `olisar` namespace.

### Changed

[77343b0] — The console was redesigned to conform to DESIGN.md.

[f66ca05] — Text buttons share one height and take exactly one variant, enforced by a design linter that runs on every build.

[84803c7] — Calculator, Dice and Concise stop being built-ins, and the catalog is grouped by category.

[c135f7a] — The Behavior page is organized into reactive and proactive halves.

[2d27662] — Reaction liberalness is a numeric threshold.

[e28a4bd] — The desktop window opens sized to the screen's work area.

[d316906] — The project is MIT licensed.

[fac52fc] — The docs site takes the console's blue and shows console-exact code previews.

### Fixed

[758e846] — A publish fails closed when the risk review can't run.

[fa3fd38] — The extension editor's autocomplete popup no longer comes up blank.

[2f385c6] — The Proactivity card hint no longer reads "umprompted".

## [0.4.2] — 2026-06-26

Passive reactions had one behavior and no dial. The threshold deciding how freely Olisar reacts was already stored and enforced; this release exposes it.

### New

[5db8c1a] — Behavior → Passive reactions gains a Liberalness control: liberal weighs every message, balanced skips trivial chatter, selective sticks to questions and prompts.

## [0.4.1] — 2026-06-23

The v0.4.0 desktop build didn't launch. Its bundle was missing every one of its data files, the built-in extensions, the sandbox and its bundled TypeScript compiler, and the dashboard itself, so the backend crashed while seeding built-ins and the app reported "the backend didn't start."

### New

[60bc35a] — The bot's Discord About Me carries a short "Powered by Olisar AI" line, disclosed in the dashboard, which caps the operator's own text at 300 characters.

### Changed

[60bc35a] — The extension-authoring pages are unified under one Extend section, in the console docs and the landing docs alike.

[ba738f5] — CI refuses to cut a release whose version files disagree with the tag.

### Fixed

[7fa06cb] — Packaging collects the data files and every bot cog by path, with a build-time guard so it can't silently regress.

[7fa06cb] — A missing built-in extension is skipped rather than taking the bot, the API and the dashboard down with it.

## [0.4.0] — 2026-06-22

Olisar gains an extension SDK and a marketplace to publish to, both authored from the console. Extensions are written in TypeScript, transpiled server-side and run in a hermetic sandbox with no ambient authority, so installing one is a decision about permissions rather than about trust.

The build published under this tag fails to launch; 0.4.1 fixes it.

### New

[ec91318] — Extensions are written in the console and run in a hermetic QuickJS sandbox with CPU, memory and wall limits.

[ec91318] — The runtime derives the executable JS from source with a vendored `typescript.js`, so it never runs untrusted compiled code.

[ec91318] — An extension can add LLM tools, slash commands with modal and button flows, knowledge and glossary seeds, a settings pane and a system-prompt note.

[ec91318] — Every built-in is an SDK extension that can be read and forked.

[ec91318] — Permissions are split into requested and granted, with an install-time consent screen.

[93c0ea4] — `.olx` bundles are source-only, content-hashed and signed with Ed25519, and an import re-transpiles and re-verifies.

[6fb04fe] — Host secrets are barred from imported and marketplace code.

[4e36010] — A Cloudflare-hosted registry serves the marketplace.

[f7f3765] — The registry runs inside free-tier R2 caps, with admin publish and deploy config.

[f4e9c21] — The console browses and installs from the marketplace.

[b1b733b] — The registry takes self-serve register, publish and yank.

[a9e053e] — Extensions publish to the marketplace from the console.

[ec4bafd] — The registry verifies a publisher through Discord.

[cc75e8d] — A verified publisher carries a badge in the console and in the bot flow.

[832ac98] — A published extension can be yanked from the console.

[5c3d50c] — In-console updates, revocation, a publisher panel and provenance columns.

[4c13811] — A six-page Build extensions guide.

### Changed

[21f8430] — CI builds the Windows release on Python 3.12, since quickjs ships no cp313 Windows wheel.

[17dfe21] — The landing page drops the MIT license claim and keeps "free and open source".

[35148e5] — The landing page footer gains a corner centerpiece with 3D parallax.

## [0.3.0] — 2026-06-20

Olisar gets the parts that make it read as a member rather than a service: an immersion pack covering presence, reactions, catch-ups and reminders, a test chat you can talk to without it remembering anything, and a bio that applies itself to Discord instead of being copy-pasted through the Developer Portal.

It also gets somewhere to send people. The landing page and a navigable docs site replace a README link.

### New

[a8eea83] — An immersion feature pack for richer, more in-character behavior.

[a8eea83] — The `get_user_status` and `who_is_in_voice` tools, per guild and behind the opt-in presence intent.

[a8eea83] — Passive emoji reactions, with a per-channel cooldown and an hourly cap.

[a8eea83] — `/catchup` digests what was said since you last spoke.

[a8eea83] — Reminders and follow-ups, with an event-dated fact creating its own follow-up.

[901e454] — A Test chat sandbox runs the full persona, knowledge base and tools with no memory, so nothing said there is saved.

[0f887b5] — Test chat folds into the Persona tab, and saving the bio applies it to Discord.

[daa11ad] — An in-app settings popup, and a Knowledge re-index with live progress.

[c7b8e49] — A Clear index button and an `/olisar clear-index` command wipe the index and halt the backfill.

[c7b8e49] — The API keys page autofills from the environment on a loopback request, and gives a remote browser nothing.

### Changed

[86d42a5] — A GitHub Pages landing page.

[490f953] — A styled docs page, linked from the landing page.

[c0cf0c9] — The setup guide lives on the docs page.

[69db051] — The setup guide and the dashboard docs are consolidated into DOCUMENTATION.md.

[23e853e] — The logos use the brand orb mark, with the shield kept for the dashboard.

[0345d23] — The security badge uses the Secure Console shield.

[c1987fa] — The download buttons know your OS, with a dropdown for the others.

### Fixed

[a8eea83] — A reasoning model's hidden thinking tokens no longer starve the visible reply into a truncated answer.

[c7b8e49] — A channel set to not indexed stops appearing as queued or indexing.

[c7b8e49] — The Knowledge card shows a live per-channel indexed count, and a finished channel keeps its place with a status chip.

## [0.2.1] — 2026-06-19

macOS was the only platform, and an update meant being handed a download page. Both change here.

### New

[f746af9] — The desktop app runs on Windows as well as macOS.

[e1d52e6] — The app updates itself in place instead of opening the download page.

[7d57192] — An operator-only control powers the bot down, behind a hold-to-confirm.

### Changed

[b2c12df] — The trust boundaries behind the Tailscale Funnel tunnel are tightened.

[f3706d9] — Unused imports and dead code are gone.

## [0.2.0] — 2026-06-19

The first published release of Olisar: a self-hosted AI Discord bot you run as a desktop app on your own machine. It runs on the free Google Gemini tier with your own keys, and everything it learns stays in a local database.

### New

[bf706af] — A Discord-OAuth admin console, reachable remotely over Tailscale Funnel with no domain and no port forwarding.

[bf706af] — Persona, persistent memory with server-wide search, a teachable knowledge base, per-channel modes, and role-based access with live permission re-checks.

[bf706af] — Member impressions, image understanding and image generation.

[bf706af] — Extensions, including a Star Citizen pack.

[07367be] — The app checks GitHub Releases on launch and offers a newer version from the tray.
