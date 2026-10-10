# Olisar documentation

Olisar is a self-hosted AI bot for Discord. It runs as your own Discord bot, from a desktop app on your computer or on a cloud server you control, and it uses your own free Google Gemini key.

This file has the same pages as the console's Docs tab, plus the setup guide. If you're new, read [What Olisar is](#what-olisar-is), then [Setup](#setup).

> [!NOTE]
> This file is generated from [web/src/docs.tsx](web/src/docs.tsx) and the Setup chapter below. Edit those, then run `node web/scripts/build-docs-site.mjs` to rebuild this file and `docs/docs.html`. Writing rules are in [web/DOCS_STYLE.md](web/DOCS_STYLE.md).

## Contents

**Start**

- [What Olisar is](#what-olisar-is)
- [Servers](#servers)
- [Talking to Olisar (for members)](#talking-to-olisar-for-members)
- [Slash commands](#slash-commands)

**Setup**

- [Install the desktop app](#install-the-desktop-app)
- [Create your Discord application](#create-your-discord-application)
- [First-run setup wizard](#first-run-setup-wizard)
- [Build & run from source](#build--run-from-source)

**Hosting & access**

- [Running multiple bots](#running-multiple-bots)
- [Hosting & your data](#hosting--your-data)
- [Host on a server](#host-on-a-server)
- [Remote access](#remote-access)
- [Console settings](#console-settings)

**Configure**

- [Persona](#persona)
- [Behavior](#behavior)
- [Models](#models)
- [Channels](#channels)
- [Access control](#access-control)
- [Member portal](#member-portal)
- [Command replies](#command-replies)
- [API keys](#api-keys)

**Knowledge & memory**

- [Knowledge base & glossary](#knowledge-base--glossary)
- [Memory & search](#memory--search)
- [Members](#members)
- [Images](#images)

**Extend**

- [Extensions](#extensions)
- [Write an extension](#write-an-extension)
- [SDK reference](#sdk-reference)
- [Commands & interactions](#commands--interactions)
- [Share extensions as files](#share-extensions-as-files)
- [The marketplace](#the-marketplace)
- [Security & trust](#security--trust)

**Reference**

- [Usage & rate limits](#usage--rate-limits)
- [Privacy & data](#privacy--data)
- [Troubleshooting](#troubleshooting)

## Start

### What Olisar is

Olisar is an AI bot for Discord that you host yourself. Members talk to it in your server, and it answers from the conversation, what it remembers about the server and its members, pages and documents you teach it, and the web.

You set it up and control it from the *console*, a web app that comes with Olisar.

#### How Olisar runs

One person, the *operator*, installs the Olisar desktop app on a Mac or Windows PC and connects it to a Discord application they create. The app runs the bot either on that computer or on a Linux cloud server (a VM), so it can stay online while the computer is off. Whatever Olisar stores stays on the machine that runs it. See [Hosting & your data](#hosting--your-data) and [Host on a server](#host-on-a-server).

Olisar writes its replies with Google's Gemini models, using the operator's own API key. The free tier is enough to run the bot, and turning on billing for the key lifts its daily limits. Image generation is optional: it uses Gemini when the key has billing on, and Cloudflare Workers AI otherwise. See [API keys](#api-keys), [Models](#models) and [Usage & rate limits](#usage--rate-limits).

One install can run several bots ([Running multiple bots](#running-multiple-bots)), and each bot can be in several servers, with separate settings for each ([Servers](#servers)).

#### Who uses what

| Who | Who that is | What they use |
| --- | --- | --- |
| Operator | The person who installed Olisar and owns the bot's Discord application (or is on its team) | The desktop app, and the whole console for every server the bot is in |
| Admin | Anyone with **Manage Server** on a server Olisar is in | The console, for the servers they manage |
| Member | Anyone in a server Olisar is in | Discord, where they [talk to Olisar](#talking-to-olisar-for-members), and the [member portal](#member-portal) if the server has opened it |

Admins sign in to the console with their Discord account. To reach it from their own computers, they need its web address, which exists once the operator turns on [remote access](#remote-access) or hosts Olisar on a server.

#### The console

The sidebar holds everything you switch between. From the top:

- The bot switcher, when the install runs more than one bot
- The server switcher, which picks the server every page configures
- A **Get started** list of what the selected server still needs, until it's done
- **Search** (<kbd>⌘K</kbd>, or <kbd>Ctrl</kbd>+<kbd>K</kbd> on Windows), which finds any page, server, settings pane or docs page
- The tabs

| Tab | What it's for | Docs |
| --- | --- | --- |
| **Persona** | Olisar's name, personality and bio in this server, and a test chat | [Persona](#persona) |
| **Behavior** | When Olisar replies, whether it joins in or reacts on its own, its model, web search and memory settings | [Behavior](#behavior) |
| **Command replies** | The text Olisar sends for slash commands and its automatic messages | [Command replies](#command-replies) |
| **Channels** | What Olisar does in each channel: read, remember, reply, or nothing | [Channels](#channels) |
| **Access** | Which roles can use Olisar, which actions need the tool PIN, and the member portal | [Access control](#access-control) |
| **Knowledge** | The knowledge base, the glossary, the message search index and **Clear memory** | [Knowledge base & glossary](#knowledge-base--glossary) |
| **Members** | What Olisar has learned about each member | [Members](#members) |
| **Extensions** | Optional features you turn on per server. The operator also writes and installs them here | [Extensions](#extensions) |
| **API keys** | The Gemini and Cloudflare keys, and the monthly budget when the key has billing on. Only the operator sees this tab | [API keys](#api-keys) |
| **Usage** | Today's model quota, or the month's spend with billing on, and what's using it, for all servers together | [Usage & rate limits](#usage--rate-limits) |
| **Docs** | This documentation | |

At the foot of the sidebar, a drawer shows whether the bot is online. Tap it or drag it up for the console's web address, the account you're signed in with, **Settings** and **Log out**. Settings holds preferences that apply to the whole app rather than one server ([Console settings](#console-settings)).

#### Save changes

Tabs with settings hold your edits until you save them. When you change something, a bar at the bottom says "You have unsaved changes". Press **Save changes** (or <kbd>⌘S</kbd>, <kbd>Ctrl</kbd>+<kbd>S</kbd> on Windows) to apply them, or **Reset** to drop them. For a few seconds after a save, **Undo** puts back what was there before.

A saved change applies from Olisar's next reply, with nothing to restart. If you leave a tab or switch servers with unsaved edits, the console asks whether to save them first.

#### Where to start

- Setting up Olisar for the first time: [Install the desktop app](#install-the-desktop-app), [Create your Discord application](#create-your-discord-application), then the [first-run setup wizard](#first-run-setup-wizard).
- Signing in as an admin of a server Olisar is already in: pick your server in the switcher and work through the **Get started** list. Every channel starts off, so Olisar says nothing in a server until you choose channels for it to reply in ([Channels](#channels)).
- A member of a server Olisar is in: read [Talking to Olisar](#talking-to-olisar-for-members).

### Servers

One Olisar bot can be in many Discord servers at once. Each server has its own settings, memory and knowledge, and the console shows one server at a time.

#### Switch servers

The server switcher at the top of the sidebar shows the server you're configuring. Open it and pick another server, and every tab then shows and saves that server's settings. The console remembers your choice in this browser. **Search** (<kbd>⌘K</kbd>) lists your servers too.

The operator sees every approved server the bot is in. Anyone else sees the servers where they had **Manage Server** when they signed in. If you've just been given Manage Server, or Olisar has just joined another server you manage, press **Log out** in the drawer at the foot of the sidebar and sign in again to see it.

Losing Manage Server takes effect right away. The server drops off your switcher, and if you lose it on every server Olisar is in, you're signed out.

#### Add Olisar to another server

1. Open the server switcher and choose **Add to a server**. To have someone else add it, choose **Copy invite link** and send them the link.
2. In Discord, pick the server and authorize the bot. Discord only offers servers where you have **Manage Server**.
3. Olisar joins. If the server is approved as it joins (see the next section), it shows up in the operator's switcher, and other admins see it after they sign in again. Otherwise it waits for the operator to approve it.

The invite asks Discord for only what Olisar uses: **View Channels**, **Send Messages**, **Send Messages in Threads**, **Read Message History**, **Embed Links**, **Attach Files** and **Add Reactions**, plus the right to add its slash commands. It doesn't ask for moderation permissions or **Mention Everyone**.

If **Public Bot** is off for the bot in the Discord Developer Portal, only the operator sees **Add to a server** and **Copy invite link**, because Discord lets only the application's owners add a private bot.

A server Olisar joins starts from default settings, not a copy of another server's. Its persona and name trigger use the name the bot has in that server, and every channel starts off, so Olisar says nothing there until someone sets channels for it on the Channels tab ([Channels](#channels)).

#### Approve a server

Anyone with **Manage Server** can add a public bot to their server, so Olisar doesn't work in a new server until the operator approves it. Three kinds of server are approved as Olisar joins:

- The main server (see below)
- The first server the bot joins
- A server whose owner is the operator

Every other server waits, including one the operator added but doesn't own. While a server waits, Olisar ignores its messages and doesn't store or index them, its slash commands don't appear there, and its admins can't sign in to the console through it.

The operator sees a notice at the top of the console saying the bot was added to that server, with two buttons:

| Button | What happens |
| --- | --- |
| **Approve** | Olisar starts working in the server, its slash commands appear there, and the server joins the switcher |
| **Leave server** | Olisar leaves the server. The bot has to be online for this |

There's no way to withdraw an approval in the console. To stop using Olisar in an approved server, remove the bot from it.

#### Remove Olisar from a server

Kick the bot from the server in Discord, as you would any other bot. The server drops off the switcher. What Olisar stored about it stays on the machine Olisar runs on, and if the bot is added back later, the server returns with its settings, memory and approval.

> [!NOTE]
> **Clear memory before you remove the bot**
> Once the bot has left, the server isn't in the switcher, so you can't clear it from the console. To erase what Olisar learned there, select the server and press **Clear memory** on the Knowledge tab first ([Knowledge base & glossary](#knowledge-base--glossary)).


#### The main server

The main server is the one you added the bot to in the [setup wizard](#first-run-setup-wizard), or the one you picked as **Main server** there if the bot was already in several. It differs from the others in three ways:

- It's approved as Olisar joins.
- DMs with Olisar use its settings (see the next section).
- Its **About me** on the Persona tab is the bot's Discord bio. The bot has one bio, so the **About me** you save on other servers isn't shown anywhere.

#### DMs

A DM isn't tied to a server, so Olisar uses the main server's persona, **Reply in DMs** setting, access rules, knowledge base and extensions when someone DMs it.

Discord lets anyone who shares a server with Olisar DM it. Someone who isn't in the main server gets replies without the main server's knowledge base, glossary or extensions, and gets no reply at all if the main server limits Olisar to certain roles ([Access control](#access-control)).

#### What's per server and what's shared

| Setting or data | Scope |
| --- | --- |
| Persona | Per server, except **About me**, which comes from the main server |
| Behavior | Per server |
| Channels | Per server |
| Access, including the member portal and which actions need the tool PIN | Per server |
| Command replies | Per server |
| Knowledge base and glossary | Per server |
| Conversation memory, member impressions and remembered facts | Per server |
| Message search index | Per server |
| Which extensions are on, and their settings | Per server |
| Installed extensions | Shared. The operator installs an extension once, and each server turns it on or off |
| API keys, including the **UEX API token** on the Star Citizen extension | Shared. Only the operator can see or change them |
| The tool PIN | Shared. One PIN for every server |
| Gemini quota and the **Usage** tab | Shared. Every server draws on the same daily quota, or with billing on, the same budget |
| **Web searches per day** (**per month** with billing on) | Per server. Each server's cap counts only its own searches, but Google's search allowance is shared |
| DMs | Follow the main server |
| A member's `/forget-me` | Covers every server Olisar is in, and DMs |

Access decides who can use Olisar within one server. It doesn't change which servers an admin can manage in the console; that's **Manage Server** in Discord.

### Talking to Olisar (for members)

Olisar is an AI bot in your Discord server. You talk to it the way you'd talk to anyone in a channel, and it answers there.

Your server's admins decide which channels Olisar replies in, and they can limit it to certain roles.

#### Get Olisar's attention

| Way | How |
| --- | --- |
| Say its name | Use its name anywhere in your message, in any capitalization: "olisar, when does the raid start?" |
| @mention it | Mention the bot in your message |
| Reply to it | Use Discord's **Reply** on one of Olisar's messages |
| DM it | Send it a direct message |
| `/ask` | Type `/ask` with your question, in any channel of the server |

By default its name is the bot's name in your server, and admins can change it or add others, such as a nickname. Mentioning it in passing usually doesn't count: Olisar answers "thanks olisar" and "olisar, you there?", but lets "olisar was down earlier" and "I already asked olisar" go by.

`/ask` works even in channels where Olisar doesn't join the chat. Everyone in the channel sees the answer. Slash commands only work inside a server, so in a DM, write to it normally. See [Slash commands](#slash-commands) for the rest.

Olisar replies to DMs unless that's been turned off. In threads and forum posts, it does whatever it does in the parent channel.

#### Point it at a message

To ask about someone else's message, use Discord's **Reply** on it and address Olisar in your reply. For example, reply to an event announcement with "olisar, is there a later one?" Olisar reads the message you replied to and uses it if it's relevant.

Olisar only looks at images attached to the message that addresses it, up to three per message. To ask about a screenshot, attach it to your question rather than replying to someone else's.

#### What you can ask for

You don't need commands for any of this. Ask in your own words.

| Ask it to | Example |
| --- | --- |
| Answer a question, using what it knows about the server | "olisar, what are the rules for the art channel?" |
| Find something posted in the server before | "olisar, where did someone post the modpack link?" |
| Look something up on the web | "olisar, when does the new season start?" |
| Look at an image | Attach a screenshot: "olisar, why won't this connect?" |
| Make an image | "olisar, draw our mascot as a pirate" |
| Catch you up on a channel | "olisar, catch me up" |
| Remember something about you | "olisar, remember that I'm on EU time" |
| Remind you later | "olisar, remind me in 2 hours to start the server" |
| Post in another channel for you | "olisar, tell #announcements the event is live" |
| DM someone for you | "olisar, DM Sam that I'll be late" |

A few limits apply:

- When Olisar searches the server, it only uses channels you can open yourself, and it links to the message its answer comes from.
- Web search and image generation may not be available on your server, and both stop for a while once their allowance is used. One message can get up to two images.
- A catch-up covers only channels where Olisar keeps the conversation.
- Reminders arrive by DM unless you ask for them in the channel. Ask "what are my reminders?" to see them, or ask it to cancel one.
- Olisar only posts in a channel for you if you could post there yourself. It sends at most 20 DMs to other people for you in a day, and only to members of the server.

Your server may also have extensions that teach Olisar more, some with their own slash commands.

#### When Olisar speaks first

If your server's admins allow it, Olisar sometimes joins a conversation without being asked, or reacts to a message with an emoji. It can also answer a "thanks" or a finished request with a reaction instead of a message. See [Behavior](#behavior).

#### When Olisar doesn't answer

- The channel isn't one Olisar replies in. Use `/ask` there, or ask an admin which channels it's in.
- Your server limits Olisar to certain roles. `/ask` tells you if you don't have access.
- You mentioned its name in passing. @mention it or reply to one of its messages instead.
- You sent a lot of requests in a short time. Olisar answers the first eight, then about one every 15 seconds. It says once that it's rate-limited and skips the rest, so ask again after a short wait.

#### Your data

Olisar keeps messages from the channels it remembers, a searchable index of messages across the server, facts it has chosen to remember about you, and a short private impression of you that shapes how it talks to you. [Privacy & data](#privacy--data) lists everything it stores.

| To | Do this |
| --- | --- |
| See what Olisar keeps and how to remove it | Run `/privacy`. Only you see the answer |
| Delete everything it has stored about you | Run `/forget-me` |
| Delete everything and stop it recording you | Run `/forget-me` with `stop_remembering: true` |
| Stop it saving your DMs with it | Run `/dm-indexing` with `enabled: false`, or tell it "stop saving my DMs" in a DM |
| See what it knows, delete single facts, or export it all | Use the [member portal](#member-portal), if your server has opened it. `/privacy` links to it |

These work even if your server limits who can use Olisar.

Olisar only searches and recalls your DMs inside that same DM. The exception is a fact it remembers about you in a DM: it's filed under the bot's main server, where its admins can see it and Olisar can bring it up. When you edit or delete a message in Discord, Olisar updates or deletes its copy.

### Slash commands

Every slash command Olisar adds to a server, who can use it, and what it does. Type `/` in a server Olisar is in to see them.

Slash commands work only inside a server, not in a DM with Olisar. A server waiting for the operator's approval has none ([Servers](#servers)). Each command's description shows the bot's own name, but the admin group is always `/olisar`.

#### Commands

| Command | Who can use it | Who sees the reply | What it does |
| --- | --- | --- | --- |
| `/ask prompt` | Members the server's access rules allow | Everyone in the channel | Asks Olisar something, from any channel |
| `/catchup [hours]` | Members the server's access rules allow | Everyone in the channel | Summarizes what you missed in this channel |
| `/privacy` | Everyone | Only you | Explains what Olisar stores and how to delete it |
| `/forget-me [stop_remembering]` | Everyone | Only you | Deletes what Olisar has stored about you |
| `/dm-indexing [enabled]` | Everyone | Only you | Turns saving of your DMs with Olisar on or off |
| `/ping` | Everyone | Only you | Shows that Olisar is online, and its latency to Discord in milliseconds |
| `/olisar watch` | Manage Server | Only you | Sets this channel to `both` |
| `/olisar unwatch` | Manage Server | Only you | Sets this channel to `off` |
| `/olisar status` | Manage Server | Only you | Shows this channel's mode |
| `/olisar learn-url url` | Manage Server | Only you | Adds one web page to the knowledge base |
| `/olisar learn-site url [depth] [max_pages]` | Manage Server | Only you | Crawls a website into the knowledge base |
| `/olisar learn-doc file` | Manage Server | Only you | Adds an uploaded document to the knowledge base |
| `/olisar sources` | Manage Server | Only you | Lists the knowledge base's sources |
| `/olisar forget-source source_id` | Manage Server | Only you | Removes one knowledge base source |
| `/olisar proactive enabled [level]` | Manage Server | Only you | Turns proactivity on or off |
| `/olisar reindex` | Manage Server | Only you | Adds older messages to the search index |
| `/olisar clear-index` | Manage Server | Only you | Empties the server's search index |
| `/killswitch extension` | Manage Server | Only you | Turns an extension off in this server at once |

Options in brackets are optional. The access rules are the roles set on the Access tab ([Access control](#access-control)), and server admins always pass them.

> [!NOTE]
> **Who can run the admin commands**
> Discord shows `/olisar` and `/killswitch` only to members with **Manage Server**. A server admin can change that under **Server Settings > Integrations** in Discord. Olisar doesn't check again, so anyone Discord lets run one of these commands can use it.


#### `/ask prompt`

Olisar answers with the same memory, search and tools it uses in chat. It works in any channel, including channels where Olisar doesn't reply to messages. If the access rules shut you out, or you've sent too many requests in a short time, only you see the refusal.

#### `/catchup [hours]`

Olisar summarizes this channel in a few bullet points. Without `hours`, it covers everything since you last posted in the channel, or the last 24 hours if you never have. It can only summarize channels where it keeps the conversation (modes `memory` and `both`; see [Channels](#channels)). Asking "catch me up" in chat does the same.

#### `/forget-me [stop_remembering]`

Deletes what Olisar has stored about you, in every server it's in and in your DMs with it: your messages, remembered facts, the impression it formed of you, your entries in the search index, your reminders and any reports of blank replies you had pending. The reply says how many messages and facts it deleted.

With `stop_remembering: true`, Olisar also stops recording you from then on, everywhere, including servers it joins later. No command turns recording back on. You can turn it back on per server in the [member portal](#member-portal), if the server has opened it.

> [!WARNING]
> The deletion can't be undone.


#### `/dm-indexing [enabled]`

With `enabled: false`, Olisar stops saving and indexing your DMs with it. It still answers your DMs, but it can't look back at what you said before. With `enabled: true`, or no option, it saves them again. What's already saved stays until you run `/forget-me`. You can also tell Olisar in a DM, "stop saving my DMs".

#### `/olisar watch`, `unwatch` and `status`

`watch` sets the channel you run it in to `both`, so Olisar reads, remembers and replies there. `unwatch` sets it to `off`. `status` shows its current mode. The other modes are on the Channels tab ([Channels](#channels)).

Run these in the channel itself. A thread or forum post follows its parent channel's mode, so running them inside one doesn't change what Olisar does there.

#### Knowledge base commands

| Command | Details |
| --- | --- |
| `/olisar learn-url url` | Needs a full `http://` or `https://` address. Olisar reads the page in the background |
| `/olisar learn-site url [depth] [max_pages]` | `depth` is how many links to follow from the start page, 0 to 3, default 1. `max_pages` is 1 to 100, default 25. Values outside those ranges are clamped |
| `/olisar learn-doc file` | PDF, DOCX, TXT or Markdown, up to 10 MB. This is the only way to add a document |
| `/olisar sources` | Lists up to 25 sources with their id, status, type and title, and the error if one failed |
| `/olisar forget-source source_id` | Removes the source with that id, as shown by `/olisar sources`, and everything read from it |

How sources are read, refreshed and searched is in [Knowledge base & glossary](#knowledge-base--glossary).

#### `/olisar proactive enabled [level]`

Turns proactivity, where Olisar joins conversations without being addressed, on (`true`) or off (`false`). `level` sets how eager it is: low (rare, high-confidence), medium (balanced) or high (chatty). Leave `level` out to keep the current one, which starts at low. The rest of the proactivity settings are on the Behavior tab ([Behavior](#behavior)).

#### `/olisar reindex` and `/olisar clear-index`

`reindex` sends Olisar back through the history of every channel it can read, in the background, so older messages become searchable. New messages are indexed as they arrive without it.

`clear-index` deletes the server's search index and stops a reindex that's running. New messages keep being indexed, and `reindex` rebuilds the history. It doesn't touch conversation memory or the knowledge base. See [Memory & search](#memory--search).

#### `/killswitch extension`

Turns an extension off in this server, effective from the next message. Pick it from the list of extensions that are on (typing its name works too), or choose **⚠ All extensions** to turn them all off. Turn an extension back on from the Extensions tab ([Extensions](#extensions)).

#### Extension commands

Extensions can add their own slash commands, such as the Star Citizen extension's `/citizen username`. Once the operator installs an extension, its commands appear in every approved server, even ones where the extension is off; there, the command replies that the extension is off. Each extension decides who can see its commands, and Discord's **Server Settings > Integrations** can change that per server. An extension can't take the name of one of Olisar's own commands. Each extension's commands are documented with it ([Extensions](#extensions)).

## Setup

### Install the desktop app

Olisar runs as a desktop app on a Mac or a Windows PC. Download the installer for your computer from the [latest release](https://github.com/gcrft123/olisar/releases/latest), install it, and open it to start the [setup wizard](#first-run-setup-wizard).

| | macOS | Windows |
| --- | --- | --- |
| Needs | macOS 13 or later, on Apple silicon | Windows 10 or later, 64-bit |
| Download | `Olisar-<version>-arm64.dmg` | `Olisar.Setup.<version>.exe` |
| First open | macOS asks you to confirm an app from the internet | SmartScreen warns about an unknown publisher |

There's no build for Intel Macs or Linux. On those, [host Olisar on a server](#host-on-a-server) or [build it from source](#build--run-from-source).

Your bots are online only while the app is running, so install it on a computer that's usually on. To keep a bot online with your computer off, choose **Server shared hosting** in the wizard and run it on a cloud server instead ([Host on a server](#host-on-a-server)).

#### Install on macOS

1. Download `Olisar-<version>-arm64.dmg` from the latest release.
2. Open it and drag **Olisar** into **Applications**.
3. Open Olisar from your Applications folder.
4. When macOS asks whether you want to open an app downloaded from the internet, choose **Open**.

The Mac app is signed and notarized by Apple, so that one prompt is all Gatekeeper shows. Run it from Applications rather than from the disk image: Olisar updates itself by replacing the copy it runs from, which it can't do on a disk image.

#### Install on Windows

1. Download `Olisar.Setup.<version>.exe` from the latest release.
2. Run it. SmartScreen shows "Windows protected your PC", because the installer isn't code-signed.
3. Choose **More info**, then **Run anyway**.

The installer doesn't ask any questions. It installs Olisar for your Windows account and opens it.

#### Close the window or quit

Closing the window doesn't stop Olisar. The app keeps running with every bot online, and its icon stays in the menu bar on a Mac or in the notification area on Windows (it may be behind the ^ overflow arrow). To bring the window back, choose **Open Dashboard** from the icon's menu, or open Olisar again.

To stop Olisar, choose **Quit Olisar** from the icon's menu. On a Mac, <kbd>⌘Q</kbd> quits too. Quitting takes every bot this app runs offline in Discord. The icon's menu is covered in [Hosting & your data](#hosting--your-data).

Olisar doesn't start on its own when you log in. After a restart, open it again, or add it to your login items on macOS or your startup apps on Windows.

#### Updates

You only install Olisar once. The app checks the releases page when it opens and every six hours after that, and installs a new release in place when you say so (see [Console settings](#console-settings)).

Releases marked **Pre-release** on that page are betas. If you install one by hand, the app follows the Beta update channel until you change it.

### Create your Discord application

Olisar runs as your own Discord bot, so it needs a Discord application of your own, made in the [Discord Developer Portal](https://discord.com/developers/applications). You can create it before you start the [setup wizard](#first-run-setup-wizard) or while it's open. The wizard links to the portal page each step needs.

Each bot needs its own application. To run several bots, create one for each ([Running multiple bots](#running-multiple-bots)).

#### Create the application

1. In the Developer Portal, press **New Application**, give it a name, and create it.
2. Open **Bot** and press **Reset Token**. Copy the token into the wizard's **Bot token** field.
3. Open **OAuth2** and press **Reset Secret**. Copy the secret into the wizard's **Client secret** field.
4. On the same **OAuth2** page, under **Redirects**, add each redirect URL the wizard shows, then press **Save Changes**.

The bot's username and avatar are set on the **Bot** page. That's the name members see in Discord, and the console calls the bot by it too.

#### What Olisar sets for you

When you paste the bot token, Olisar reads your application and changes what it can.

| Setting | What Olisar does |
| --- | --- |
| **Message Content Intent** and **Server Members Intent** | Turns them on. Discord lets an application do this itself while it's in fewer than 100 servers. Past that, the wizard asks you to turn them on under **Bot > Privileged Gateway Intents** and waits until they're on. |
| **Presence Intent** | Leaves it alone. Olisar doesn't need it. |
| Client ID | Reads it from the token, so you don't copy it. |
| Invite link | Builds the wizard's **Add to Discord** link, with only the permissions listed below. |
| **Add App** on the bot's Discord profile | If it would add only the bot's commands, Olisar makes it add the bot too, with the same permissions. A custom install link is left as it is. |

#### What you set yourself

| Setting | Where | What to do |
| --- | --- | --- |
| Redirect URLs | **OAuth2 > Redirects** | Add each one the wizard shows and press **Save Changes**. Discord doesn't let an application register its own, so the wizard waits until Discord lists them. |
| **Requires OAuth2 Code Grant** | **Bot** | Leave it off. With it on, the invite link doesn't work, and the wizard says so. |
| **Public Bot** | **Bot** | On (Discord's default), anyone with the invite link can add the bot to their server, and Olisar waits for your approval before it works there ([Servers](#servers)). Off, only you can add it. |

#### Permissions the invite asks for

The **Add to Discord** link adds the bot with the `bot` and `applications.commands` scopes (the second one is for slash commands) and these permissions:

| Permission | What Olisar uses it for |
| --- | --- |
| **View Channels** | Seeing the channels it's allowed in |
| **Send Messages** | Replying |
| **Send Messages in Threads** | Replying in threads and forum posts |
| **Read Message History** | Reading the conversation it's replying to |
| **Embed Links** | Posting embeds, such as extension replies |
| **Attach Files** | Posting generated images |
| **Add Reactions** | Reacting to messages |

It asks for nothing else: no roles, moderation, webhooks, pins or voice. It also leaves out **Mention Everyone**, so Olisar can't ping @everyone or @here in a server until that server's admins give its role that permission and allow those pings in [Behavior](#behavior).

#### Who owns the application

The Discord account that owns the application is Olisar's *operator*. The operator can manage every server the bot is in, and only the operator can change install-wide settings such as the API keys ([Hosting & your data](#hosting--your-data)).

If you create the application under a Discord developer team, the team's owner and every member who has accepted the invite count as operators, except members with the Read-only role.

### First-run setup wizard

The setup wizard connects Olisar to your Discord application and your server. It opens the first time you start the [desktop app](#install-the-desktop-app), and again for each bot you add ([Running multiple bots](#running-multiple-bots)).

Before you start, create your application in the Discord Developer Portal and keep it open in your browser ([Create your Discord application](#create-your-discord-application)). The wizard checks each value as you paste it, so there's no **Test** button: a line under the field confirms it once Discord or Google accepts it. If a step isn't finished, **Continue** says what's missing.

#### Choose where Olisar runs

The first step asks where the bot runs. Your choice decides which steps follow, and changing it later means moving the bot from **Settings > Bots**.

| Choice | Where the bot runs | How the wizard changes |
| --- | --- | --- |
| **Local unshared hosting** | On this computer. The console opens only here ([Hosting & your data](#hosting--your-data)). | The basic steps below |
| **Local shared hosting** | On this computer, with the console at an `https://…ts.net` address so other admins can sign in from anywhere ([Remote access](#remote-access)) | Adds a remote access step and a second redirect URL |
| **Server shared hosting** | On a Linux cloud server, online with this computer off ([Host on a server](#host-on-a-server)) | No redirect URL, a required Gemini key, and a deploy step at the end |

#### Go through the steps

1. Pick where Olisar runs and press **Continue**.
2. On the portal's **Bot** page, press **Reset Token**, and paste the token into **Bot token**. The wizard shows "Connected as" and the bot's name, then turns on the intents the bot needs.
3. For **Local shared hosting** only: paste a reusable **Tailscale auth key**, change the **Device name** if you like, and press **Enable remote access**. The step shows **Live** and the console's web address once it's up.
4. On the portal's **OAuth2** page, press **Reset Secret**, and paste the secret into **Client secret**. The wizard shows "Secret matches".
5. Except for **Server shared hosting**: on the same **OAuth2** page, add the redirect URL the wizard shows (two, for **Local shared hosting**) under **Redirects**, and press **Save Changes** in the portal.
6. Press **Add to Discord**, pick your server, and authorize the bot. The wizard shows "In" and the server's name once the bot joins.
7. Paste a **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey). The wizard shows "Key works · free tier" or "Key works · billing on". On a free key it adds that Google may use what members send to improve its products (except in the EEA, UK and Switzerland), and that turning on billing stops that ([Privacy & data](#privacy--data)).
8. Press **Finish & start the bot**. For **Server shared hosting**, fill in the deploy step and press **Deploy to server** instead.

**Back** returns to the previous step and keeps what you've entered. If the bot token step warns about **Public Bot** or **Requires OAuth2 Code Grant**, see [Create your Discord application](#create-your-discord-application).

#### Redirect URLs

Discord sends you back to Olisar after you sign in, but only to an address registered on the application's **OAuth2** page. An application can't register its own, so this is the one step you do by hand. Each URL switches from **Copy** to **Added** once Discord lists it, and **Continue** stays unavailable until every one has.

| Choice | Redirect URLs |
| --- | --- |
| **Local unshared hosting** | `http://127.0.0.1:8723/auth/callback` |
| **Local shared hosting** | That one, plus the `https://…ts.net/auth/callback` address from the remote access step |
| **Server shared hosting** | None in the wizard. The server's control panel shows its own after the deploy. |

If a URL doesn't switch to **Added**, check that you pasted it exactly and pressed **Save Changes**.

#### Add the bot and your keys

Adding a bot to a server takes **Manage Server** there. **Copy link**, next to **Add to Discord**, copies the same invite so you can send it to whoever manages the server. If the bot is already in more than one server, pick its **Main server**; that server's persona and settings also apply in DMs. [Servers](#servers) covers adding and approving more servers.

Olisar can't reply without a Gemini key. With either local choice you can leave the field empty and add the key later on [API keys](#api-keys). **Server shared hosting** requires it, because the server can't start without one. A free key is enough, and you can turn on billing later ([Models](#models)). The Cloudflare keys for image generation aren't part of the wizard; you add them on API keys too.

#### Deploy to a server

With **Server shared hosting**, the last step installs Olisar on your cloud server over SSH. Give your cloud provider the SSH public key the step shows when you create the server, then enter the server's **VM public IP address** and a **Tailscale auth key**, and press **Deploy to server**. It takes a few minutes; keep the window open.

If another of your bots already runs on a server, the step offers that server next to **A new server**. Pick it and there's nothing to create, though this bot still needs its own Tailscale auth key. To adopt a server where Olisar already runs, press **Connect to existing server** on the first step instead. [Host on a server](#host-on-a-server) covers both, and creating a free server on Oracle Cloud.

#### After you finish

With either local choice, the bot starts and the window changes to the sign-in screen. Press **Continue with Discord**; in the desktop app, Discord's sign-in opens in your web browser, and the app signs you in once you approve it there. Use the account that owns the application, or any account with **Manage Server** on a server the bot is in.

The console opens with a **Get started** list in the sidebar, under the server switcher. Each item ticks itself off when it's done, and the list goes away once the required ones are.

| Item | Why it's there |
| --- | --- |
| **Choose reply channels** | Every channel starts off, so Olisar ignores the server until you set a channel to reply ([Channels](#channels)) |
| **Add a Gemini key** | Shown until a Gemini key is saved |
| **Turn on images** | Optional. Done once Gemini makes images on a key with billing on, or once Cloudflare keys are saved ([Images](#images)) |

With **Server shared hosting**, the window becomes the server's control panel, **Your Olisar server**. Add the **Redirect URL** it shows to **OAuth2 > Redirects** in the portal, then press **Open console**.

#### Troubleshooting

#### "Discord didn't accept that token"

The token was mistyped, or it was reset again after you copied it. Press **Reset Token** on the **Bot** page and paste the new token.

#### The wizard asks you to turn on intents

Discord didn't let Olisar turn on **Message Content Intent** and **Server Members Intent** itself, which happens once an application is in 100 or more servers. Turn them on under **Privileged Gateway Intents** on the **Bot** page. The wizard notices within a few seconds.

#### Sign-in fails after setup

Discord only accepts a redirect URL that's registered exactly. The desktop app serves its console on port 8723, but if another program is using that port when Olisar opens, Olisar picks a different port, and the local redirect URL changes with it. Quit the program using port 8723 and reopen Olisar.

### Build & run from source

This page is for developers working on Olisar itself. It covers running the backend and console from a checkout, working on the console with hot reload, running the tests, and building the desktop installers.

#### Requirements

| Tool | Version | Used for |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | Recent | The Python environment and dependencies |
| Python | 3.13 on macOS, 3.12 on Windows | The backend. Let uv install it (see below). |
| Node.js | 22.12 or newer | The console, and Electron for the desktop app |
| Go | The version in `desktop/funnel-sidecar/go.mod` | Only for the remote access helper in a desktop build |

`quickjs`, which runs extensions, has no prebuilt package for Python 3.13. On macOS, uv compiles it from source, which needs the Xcode Command Line Tools (`xcode-select --install`). On Windows it doesn't compile, so use Python 3.12 there. Run the commands on this page in a bash shell; on Windows, Git Bash works.

#### Set up a checkout

From the repo root, create the environment on uv's own build of Python, install the dependencies with every extra, and build the console once:

```bash
uv venv --managed-python --python 3.13   # --python 3.12 on Windows
uv sync --all-extras
cd web && npm install && npm run build && cd ..
```

Plain `uv sync` leaves out the extras and removes them if they're installed. The `knowledge` extra holds the website crawler and the PDF and DOCX readers, which the bot needs; `dev` holds PyInstaller, Ruff and Alembic. `npm run build` runs the design lint and a type check, then writes the console to `web/dist`, which the backend serves.

#### SQLite extensions on macOS

Olisar loads `sqlite-vec` into every database connection through SQLite's extension API. On a Python built without that API, the backend fails on its first database access, and `desktop/backend.spec` refuses to build a bundle. Apple's system Python and the python.org installer for macOS both ship with it turned off. uv's own builds of CPython have it on, and CI builds the macOS release on one, which is why the first command above asks for one.

To check an environment, run this. It has to print `True`:

```bash
uv run python -c "import sqlite3; print(hasattr(sqlite3.connect(':memory:'), 'enable_load_extension'))"
```

#### Run the backend

```bash
uv run python -m olisar.runtime
```

This runs one bot, serving the console at `http://127.0.0.1:8000`. On startup it prints a link of the form `http://127.0.0.1:8000/auth/local?token=…`. Open that link rather than the bare address. The setup wizard, remote access and the other controls that only work on the machine Olisar runs on need this launch's local token (the desktop app sends it for you), and the link hands it to your browser as a cookie. The token is also written to `local-token` in the data folder.

| Option | Default | What it does |
| --- | --- | --- |
| `--port N` | `8000`, or `OLISAR_PORT` | The port to serve the console on |
| `--host H` | `127.0.0.1`, or `OLISAR_HOST` | The address to listen on |
| `--gateway` | Off | Runs every bot on the install, each in its own process, behind one console with the bot switcher. This is how the desktop app runs. Without it, one bot runs, as in the server's Docker image. |

| Environment variable | What it does |
| --- | --- |
| `OLISAR_DATA_DIR` | Where the database, uploads and bots live. Defaults to `data/` in the checkout. |
| `OLISAR_LOCAL_TOKEN` | Uses this value as the local token instead of making one, and skips printing the link |

Run it from the repo root. A `.env` file there (see `.env.example`) is read at startup, and the setup wizard fills its fields from it while the install isn't set up yet.

#### Work on the console with hot reload

Run the backend with a local token you choose, and the Vite dev server in a second terminal. Vite proxies `/api` and `/auth` to port 8000.

```bash
OLISAR_LOCAL_TOKEN=dev uv run python -m olisar.runtime
```

```bash
cd web && npm run dev
```

Then open `http://localhost:5173/auth/local?token=dev`. Signing in through Vite redirects to `http://localhost:5173/auth/callback`, so register that URL in the Discord application too.

#### Run the tests

There's no `tests/__init__.py`, so `unittest discover` doesn't find the tests. Name the modules instead:

```bash
uv run python -m unittest $(ls tests/test_*.py | sed 's#/#.#; s#\.py$##')
```

One file runs on its own with `uv run python -m unittest tests.test_gateway -v`. Some tests exercise failure paths and print tracebacks as they pass; `OK` on the last line is what counts.

#### Build the desktop installer

Build each part in order, from the repo root. The macOS commands are shown; the Windows equivalents follow.

1. Build the console:

```bash
cd web && npm install && npm run build && cd ..
```

2. Bundle the backend with PyInstaller. It writes `dist/olisar-backend/`:

```bash
uv run pyinstaller desktop/backend.spec --noconfirm --clean
```

3. Optionally, build the remote access helper. Without it the app runs, but remote access reports that the helper is missing:

```bash
cd desktop/funnel-sidecar && GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o ../resources/olisar-funnel . && cd ../..
```

4. Build the installer. It writes to `desktop/out/`:

```bash
cd desktop && npm install && npm run dist:mac
```

On Windows, build the helper with `go build -ldflags="-s -w -H windowsgui" -o ../resources/olisar-funnel.exe .` in `desktop/funnel-sidecar`, and the installer with `npm run dist:win`. `npm run dist:mac` signs the app if a Developer ID Application certificate is in your keychain; without one it logs a warning and builds an unsigned `.dmg`. Signing, notarizing and publishing a release are covered in `RELEASING.md`.

#### Run the desktop app without packaging

After step 2 above, start the Electron shell from the checkout. It runs the backend bundle in `dist/olisar-backend/` and reads `.env` from the repo root.

```bash
cd desktop && npm install && npm start
```

> [!WARNING]
> **It uses the installed app's data**
> An unpackaged run uses the same data folder as the installed Olisar (`~/Library/Application Support/Olisar` or `%APPDATA%\Olisar`), so it runs your real bots on their real data. It also can't run alongside the installed app: while that's open, `npm start` only brings its window forward. Quit the installed app first.

## Hosting & access

### Running multiple bots

One install of the desktop app can run several bots at the same time. Each bot is a separate Discord application, with its own name, avatar, token, servers, settings and memory. That's different from one bot being in several Discord servers, which [Servers](#servers) covers.

You manage bots from the desktop app on the computer it runs on. A console opened over a [web link](#remote-access), and the console of a bot hosted on a [cloud server](#host-on-a-server), don't show any of the controls on this page.

#### How bots run side by side

Every bot on the install is online at once, each in its own process. If one bot crashes or is busy, the others keep running, and no bot can read another's settings or keys. The console shows one bot at a time. Switching bots changes what you're looking at; it doesn't stop or restart anything.

| Each bot has its own | Shared by every bot on the install |
| --- | --- |
| Discord application, token and client secret | The desktop app and its version |
| Servers it's in, and each server's persona, behavior, channels and access rules | The [update channel](#console-settings) |
| Memory, member profiles, knowledge base and glossary | The data folder, where each bot you add gets a subfolder (see [Hosting & your data](#hosting--your-data)) |
| [API keys](#api-keys) for Gemini, Cloudflare and UEX | The local sign-in address the setup wizard shows |
| [Tool PIN](#console-settings) and console sign-ins | |
| [Web link](#remote-access) and Tailscale device name | |
| Where it runs: this computer or a cloud server | |

Two bots can use the same Gemini key, but Google's daily limits are counted per Google Cloud project, so they share one allowance. With billing on they share one bill, and each bot's budget counts only what that bot spends. See [Usage & rate limits](#usage--rate-limits).

#### Add a bot

Each bot needs its own application in the [Discord Developer Portal](https://discord.com/developers/applications). One Discord account can create several applications, so you don't need a second account. See [Create your Discord application](#create-your-discord-application).

1. Open **Settings > Bots** and press **Add a bot**. With two or more bots, you can also open the bot switcher at the top of the sidebar and choose **Add a bot**.
2. Type a name for the bot and press **Add bot**. The name is only a label in Olisar; it doesn't change anything in Discord.
3. The console switches to the new bot and opens its [setup wizard](#first-run-setup-wizard). Go through it with the new application's token and client secret.

On the wizard's sign-in step, register the redirect URL it shows. On this computer it's the same address for every bot. The wizard also suggests a Tailscale device name based on the bot's name, so each bot's web link gets its own address.

> [!WARNING]
> **Give each bot its own Discord application**
> Don't set up two bots with the same token, and don't run the same bot here and on a cloud server at once. Olisar doesn't check for this, and both copies would connect to Discord at the same time, each with its own memory and settings.


#### Switch between bots

With two or more bots, the top of the sidebar shows the bot you're looking at and its status. Open it to pick another bot, add one, or choose **Manage bots** to open **Settings > Bots**. The setup, sign-in and server panel screens show the same menu in their top-left corner, so a bot you haven't finished setting up never strands you.

Each bot keeps its own sign-in. The first time you open a bot, you sign in to it with Discord; after that, switching back doesn't ask again.

The bot on screen is one choice for every console open on this computer. If you switch in the app window, a browser tab that was showing another bot reloads onto the new one. Admins who reach a bot over its web link always land on that bot, whichever one you're looking at.

When the app starts, it opens on the bot marked **Open on launch**.

#### Manage bots

**Settings > Bots** lists every bot with these controls. They stay in the same place on every row, and a control that doesn't apply to a bot is greyed out.

| Control | What it does |
| --- | --- |
| **Open** | Shows this bot in the console. Not available for the bot you're already on, which is marked **Current**. |
| **Retry** | Takes the place of **Open** for a bot that couldn't start, and starts it again now. |
| Star (**Open on launch**) | Makes this the bot the app opens on when it starts. A filled star marks the current choice. |
| Pencil (**Rename**) | Changes the bot's name in Olisar. Its name in Discord stays the same. |
| **Move / change hosting** | Moves the bot between this computer and a cloud server, or to another server. See [Host on a server](#host-on-a-server). Only for a bot that's running and set up. |
| Eraser (**Reset configuration**) | Clears the bot's Discord credentials, API keys, hosting and remote access, and takes it offline, so you can set it up again. Its persona, memory, knowledge and settings stay. |
| **Delete** | Stops the bot and permanently deletes everything this app stores for it, including its token, settings and memory. |

Reset and Delete both ask you to type the bot's name to confirm, and neither can be undone. You can't delete the bot you're looking at or your only bot. For a bot that runs on a cloud server, Reset and Delete only affect this app: the server keeps running until you stop it there.

#### Bot status

The switcher and **Settings > Bots** show where each bot stands. A healthy bot shows no status in the list.

| Status | Means |
| --- | --- |
| Online | Connected to Discord |
| Connecting… | Started and signing in to Discord |
| Starting… | Its process is starting up |
| Not set up | Its setup wizard hasn't been finished |
| On a server | It runs on a cloud server. Hover its name to see which one. |
| Offline | Powered off from the console |
| Can't connect | Discord refused it, usually because an intent is off or the token was reset. See [Troubleshooting](#troubleshooting). |
| Couldn't start | Its process failed to start three times in a row |

#### A bot couldn't start

Olisar restarts a bot whose process exits, waiting a little longer after each failure. After three failed starts in a row, the bot shows **Couldn't start**, and opening it shows the last lines it printed with a **Try again** button. Your other bots keep running. If it keeps failing, send those lines with a bug report from **Settings > Feedback**.

### Hosting & your data

Olisar runs on a computer you control, either your own Mac or PC through the desktop app, or a Linux cloud server. Whichever you pick, that machine runs the bot, serves the console and keeps all of its data. There's no Olisar cloud service holding your bot.

#### Where Olisar runs

You pick one of three ways in the first step of the [setup wizard](#first-run-setup-wizard). Each bot on an install makes its own choice.

| | On this computer | Shared over Tailscale | On a cloud server |
| --- | --- | --- | --- |
| Wizard option | **Local unshared hosting** | **Local shared hosting** | **Server shared hosting** |
| Where the bot runs | This computer | This computer | A Linux VM |
| Online while this computer is off or asleep | No | No | Yes |
| Where admins open the console | Only on this computer | Anywhere, at a `…ts.net` web link | Anywhere, at the server's `…ts.net` web link |
| What you need besides the app | Nothing | A free Tailscale account | A Linux VM (free on Oracle Cloud) and a free Tailscale account |
| [Member portal](#member-portal) and **Report this** links | Not available | Available | Available |

You can change your mind later. Turn the web link on or off for a bot on this computer under **Settings > Remote access** (see [Remote access](#remote-access)), and move a bot between this computer and a server with **Move / change hosting** under **Settings > Bots** (see [Host on a server](#host-on-a-server)).

#### The menu-bar app

On a Mac, Olisar lives in the menu bar; on Windows, in the system tray. Closing the console window doesn't stop anything: every bot keeps running, and the icon stays. Its menu has these items.

| Item | What it does |
| --- | --- |
| **Open Dashboard** | Opens the console window |
| Backend status | Shows **Backend: online** once Olisar is running |
| **Remote:** and the address | Shows the web link while remote access is on |
| **Enable remote access** / **Disable remote access** | Turns the web link on or off for the bot on screen. Appears once remote access has been set up. |
| **Refresh status** | Reads the status lines again |
| **Install update & restart**, **Download update** or **Check for Updates…** | Installs or downloads a newer version, or checks for one. See [Console settings](#console-settings). |
| **Quit Olisar** | Stops every bot on this computer and closes the app |

Bots on this computer are online only while Olisar is running and the computer is awake. Olisar doesn't keep the computer awake and doesn't open itself at login, so set the computer not to sleep and open Olisar again after a restart. Bots hosted on a cloud server don't depend on this computer at all.

To hide the icon, turn off **Show in the menu bar** under **Settings > Desktop app**. Opening Olisar again brings the window back.

#### Where your data lives

Everything a bot knows lives in its database: its Discord token and API keys, every server's settings, memory, member profiles, the search index and the knowledge base. Uploaded knowledge-base files sit next to it.

| Where Olisar runs | Data folder |
| --- | --- |
| Desktop app on macOS | `~/Library/Application Support/Olisar` |
| Desktop app on Windows | `%APPDATA%\Olisar` |
| Cloud server | A Docker volume on the VM, one per bot. The bot's credentials are in the `.env` file in its folder, `~/olisar` for the first bot. |

In the desktop app's folder, the first bot's database is `olisar.db`, and each bot you add gets a folder of its own under `profiles`. Before a new version changes a database, Olisar copies it to `olisar.db.pre-<version>` beside it and keeps the two most recent copies. Moving a bot leaves its old copy behind as a backup too.

To back up a bot on your computer, quit Olisar from the menu-bar icon first, then copy the whole folder.

> [!WARNING]
> **The data folder holds your secrets**
> Bot tokens and API keys are stored in the folder unencrypted, so anyone with a copy can run your bots. Keep backups somewhere private.


What Olisar stores about members, and what it sends to Google to reply, is on [Privacy & data](#privacy--data).

#### The operator and admins

The *operator* is the person who runs Olisar, recognized by owning the bot's Discord application. Everyone else who manages it is an *admin*: anyone with **Manage Server** on a server Olisar is in. Admins don't install anything; they sign in to the console with their own Discord account.

| | Operator | Admin |
| --- | --- | --- |
| Who | The owner of the bot's Discord application, or a member of its Developer Portal team (except read-only members). On a cloud server, also any Discord user ID listed in `ADMIN_ALLOWLIST` in its `.env` file. | Anyone with **Manage Server** on a server Olisar is in, once the operator has [approved](#servers) that server |
| Servers they see | Every server the bot is in | Only the servers where they have **Manage Server** |
| Only they can | Approve new servers, use the API keys tab, power the bot off and on from the sidebar drawer, set the [tool PIN](#console-settings), read **Logs** and **Activity**, change the update channel, and write or import [extensions](#extensions) | |

The sidebar drawer shows which you are: **Allowlisted admin** for the operator, **Manage-server admin** for everyone else.

Olisar checks this again while you work. Losing **Manage Server** in Discord removes that server from your console on your next action, and losing it everywhere signs you out. An operator removed from the application's team loses operator access within five minutes. While the bot is powered off, Olisar can't check, so other admins have to sign in again every five minutes. A sign-in otherwise lasts 14 days.

#### Sign in to the console

On the computer running the desktop app, open the window and press **Continue with Discord**. Your browser opens on Discord's sign-in page; finish there, then come back to the app.

From anywhere else, open the bot's web link, either from [remote access](#remote-access) or from a [cloud server](#host-on-a-server), and press **Continue with Discord**. An account that isn't the operator or an admin sees **Access denied**, unless the [member portal](#member-portal) is open on one of its servers, in which case it lands there instead.

### Host on a server

Hosting a bot on a cloud server keeps it online around the clock, whether your computer is on or not. The server runs the same Olisar as the desktop app, and its console gets a public `https://…ts.net` address that you and your admins sign in to with Discord.

The easiest way is to let the desktop app install Olisar on the server for you over SSH. You can also set it up from a terminal on the server, without the desktop app.

#### Requirements

- A Linux VM with a public IP address that you can reach over SSH. Olisar runs on both x86-64 and Arm. [Oracle Cloud's Always Free](https://www.oracle.com/cloud/free/) Arm VM costs nothing, and the steps are below.
- For setup from the desktop app, a login user called `ubuntu` that can use `sudo` without a password. Ubuntu images on Oracle Cloud and AWS come with one. We recommend Ubuntu.
- Your Discord application's bot token and client secret. See [Create your Discord application](#create-your-discord-application).
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey). A server can't start without one.
- A free [Tailscale account](https://login.tailscale.com/start) and a reusable auth key from [Settings > Keys](https://login.tailscale.com/admin/settings/keys). This gives the server's console its address, with no domain and no open ports. The first time, Tailscale may ask you to turn on Funnel for your tailnet; see [Remote access](#remote-access).

#### Set it up from the desktop app

1. In the [setup wizard](#first-run-setup-wizard), pick **Server shared hosting** and go through the steps. For another bot on an install that already has one, add it first under **Settings > Bots** (see [Running multiple bots](#running-multiple-bots)).
2. On the **Deploy** step, copy the **SSH public key** it shows. The matching private key never leaves your computer.
3. Create the VM and give it that key, following the Oracle Cloud steps below or the notes on other providers.
4. Paste the VM's public IP into **VM public IP address**.
5. Paste your Tailscale auth key into **Tailscale auth key**.
6. Press **Deploy to server** and keep the window open. Olisar installs Docker on the VM if it isn't there, writes the bot's configuration, and starts the newest release on your [update channel](#console-settings). This takes a few minutes.
7. When the deploy finishes, the window becomes the server panel. It shows a **Redirect URL**: add it under **Redirects** on your application's **OAuth2** page in the [Discord Developer Portal](https://discord.com/developers/applications) and press **Save Changes**. The panel ticks it off once Discord lists it.
8. Press **Open console** and sign in with Discord.

If the deploy fails, the step shows the error and the install log, with a button to send both to the Olisar team.

You don't need to keep the desktop app open afterwards: the bot runs on the server either way. Open the app now and then, because that's when it updates the server (see Updates below).

#### Create a free VM on Oracle Cloud

Oracle Cloud's Always Free tier includes an Arm VM (the `VM.Standard.A1.Flex` shape) that runs Olisar at no cost. Signing up asks for two things worth knowing first:

- A credit or debit card, to verify your identity. Oracle doesn't charge it for Always Free resources unless you upgrade your account, though it may place a small temporary hold.
- A home region. You pick it at sign-up and can't change it later, and Always Free servers can only be created there, so choose one near you.

Then create the VM:

1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/) and sign in to the Oracle Cloud console.
2. Open the navigation menu, go to **Compute > Instances**, and press **Create instance**.
3. Under **Image and shape**, change the image to **Canonical Ubuntu** and the shape to **VM.Standard.A1.Flex** (under **Ampere**). Keep 1 OCPU and 6 GB of memory, which stays inside the Always Free allowance.
4. Under **Networking**, keep a public subnet and set **Assign a public IPv4 address** to **Yes**.
5. Under **Add SSH keys**, choose **Paste public keys** and paste the key from Olisar's **Deploy** step.
6. Press **Create**.
7. When the instance is running, copy its **Public IP address** from the instance's details page.

If Oracle says it's out of host capacity, it has no free Arm servers left in that availability domain for now. Pick a different availability domain, or wait a while and try again.

> [!NOTE]
> **Oracle can reclaim idle free servers**
> Oracle may reclaim an Always Free instance whose CPU, network and memory use all stay under 20% for 7 days. A quiet bot can look idle by that measure. See Oracle's [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) page for the current rules.


#### Use another provider

Any provider works if the VM meets the requirements above. Create the VM with the key from the **Deploy** step in the provider's SSH keys box. If the VM already exists, add the key on its own line to `~/.ssh/authorized_keys` for the `ubuntu` user. Then continue from step 4 of the desktop app steps.

#### The server panel

Once a bot runs on a server, opening it in the desktop app shows its server panel, **Your Olisar server**, instead of the console. It reads the server's state over SSH every 15 seconds.

| Status | Means |
| --- | --- |
| Running | The server is up and passing its health check |
| Starting… | The server is starting |
| Updating… | The app is moving the server to a new release |
| Powered down | The bot was powered off from its console, but the server is up. Press **Turn on**. |
| Stopped | The server was stopped. Press **Start server**. |
| Unhealthy | The server is running but failing its health check. Check **Settings > Logs**. |
| Unreachable | The app can't reach the VM. It keeps trying; if the VM's IP changed, use **Reconnect**. |

Below the status are the console's address, the server's IP, the version it runs and its uptime. **Open console** opens the console in your browser, and **Stop server** or **Start server** stops or starts the whole server. The **More** menu holds **Reconnect**.

When the server is running and Discord lists its redirect, the panel shrinks into the top-left corner, and the window fills with what the bot has been doing: who it answered, who joined, what it learned and the sources it read. Select one to read it in full. Anything that needs your attention brings the full panel back, including these.

| The panel says | What to do |
| --- | --- |
| Discord refuses the bot because an intent is off | Press **Turn on and restart**. If Discord won't let Olisar turn it on itself, the panel links to the switch in the Developer Portal. |
| Your console can't be reached | Tailscale refused the auth key, most often because it expired or was already used. Paste a new key and press **Use key**. |
| **Redirect URL** | Discord doesn't list the console's sign-in address yet. Register it as in step 7 above. |

**Settings** from the panel includes **Logs**, which shows the server's own logs, and **Remote access**, where you can rename the server's Tailscale device to change its address (see [Remote access](#remote-access)).

#### Set it up from a terminal

Use this if you don't use the desktop app. Besides the requirements above, you'll need your Discord application's client ID, and your own Discord user ID: in Discord, turn on **Developer Mode** under **Settings > Advanced**, then right-click your name and choose **Copy User ID**.

1. Connect to the VM over SSH.
2. Run the setup script:

```bash
curl -fsSL https://raw.githubusercontent.com/gcrft123/olisar/main/deploy/bootstrap.sh | bash
```

3. Answer its questions: bot token, client ID, client secret, your Discord user ID, Gemini API key and Tailscale auth key. It installs Docker, starts the newest stable release, and waits for the console's address, which can take about two minutes.
4. When it prints the console's address and a Discord OAuth redirect, add the redirect under **Redirects** on your application's **OAuth2** page in the Developer Portal and press **Save Changes**.
5. Open the address, press **Continue with Discord**, and sign in with the account whose ID you entered.
6. If the bot isn't in a server yet, the console says **No servers yet**. Press the button to add it to one.

If the script can't read the address, the cause is most often an auth key that's invalid or already used, or Funnel not being on for your tailnet. Run `sudo docker compose logs -f` in `~/olisar` to see what Tailscale said.

To manage this server from the desktop app later, connect the app to it as described next.

#### Connect the desktop app to an existing server

Connecting adopts a server that already runs Olisar, without reinstalling anything or changing its persona, memory or settings. Use it after you reinstall the desktop app or switch computers, for a server you set up from a terminal, or when the VM's IP address changes.

1. On the first step of the setup wizard, press **Connect to existing server**. For a bot that's already set up, use **Reconnect** in the server panel's **More** menu instead.
2. Enter the **VM public IP address**.
3. If this app has never connected to the VM, open **Can't connect? Add this app's SSH key to the VM**, add the key shown to the VM's `~/.ssh/authorized_keys`, and check **SSH user**.
4. Press **Connect** (or **Reconnect**).
5. If the server runs more than one bot, choose which one under **Which bot is this?** and press it again.

#### Move a bot to or from a server

To move a bot that's already set up, open **Settings > Bots** and press **Move / change hosting** on its row. Under **Move to**, choose **This computer (local)**, a server another of your bots runs on, or **A new server** (**A different server** for a bot that's already on one) and enter its **Destination VM public IP**. Then press **Move bot** and keep the window open for a few minutes.

The bot's persona, memory, knowledge and uploaded files move with it. The old copy stays where it was as a backup: a bot moved off your computer leaves its old database in the data folder, and a server a bot moved off is stopped but not deleted.

A bot moved to your computer starts with remote access off; turn it on under **Settings > Remote access**. A bot moved to a server that never had remote access has no Tailscale key yet, so the server panel asks for one.

Don't run the same bot on your computer and on a server at once. See [Running multiple bots](#running-multiple-bots).

#### Several bots on one server

One VM can run all your bots. When you deploy another bot, the **Deploy** step offers the server your other bot runs on, alongside **A new server**. Choose it and there's no VM to create and no SSH key to paste: Olisar lets the new bot into that server itself. The new bot still needs a Tailscale auth key; create a new one, since the first bot's key may have expired or been single-use. **Move / change hosting** offers the same choice.

Each bot on the VM is its own install, in its own folder (`~/olisar` for the first, `~/olisar-<id>` for the rest), with its own configuration, data and web address. Stopping, updating or moving one leaves the others alone.

#### Updates

The desktop app keeps each server on the same release as the app. Whenever the app starts on a newer version than a server, which is every launch after the app updates itself, it moves the server onto the newest release on your [update channel](#console-settings). The server panel says **Updating…**, and so does the drawer at the bottom of the server console's sidebar. The console is unreachable for a short while as the new version starts.

If the new version doesn't pass its health check, the server goes back to the previous one, and the panel says so with a **Report it** link. Your data is kept either way. A stopped server gets the new release but stays stopped, and starts on it the next time you press **Start server**.

The server follows the app's channel, so on **Beta** it runs the betas too. Switching back to **Stable** never moves it backwards: it stays on its beta until a newer stable release is out.

There's no update button in the panel, and a server's own console can't install updates. Without the desktop app, update from a terminal on the VM, in the bot's folder:

```bash
cd ~/olisar && ./olisar-update.sh
```

It installs the newest stable release, checks that it comes up healthy, and goes back to the previous release if it doesn't. It never moves the server to an older release. Add `--tag` and a release, like `--tag v2.1.beta-1`, to install that release instead, and `--force` to allow an older one. On a VM with several bots, run it in each bot's folder.

#### Troubleshooting

#### "Couldn't reach the VM over SSH"

Check that the IP address is right and the VM is running. Then check that the VM has this app's SSH key for the `ubuntu` user, either from creating the VM or in `~/.ssh/authorized_keys`.

#### The VM answered with a different SSH host key

Olisar remembers each server's SSH host key from the first connection and refuses to send anything to a server that answers with another one. If you rebuilt or replaced the VM at the same address, open **Settings > Bots**, press **Reset configuration** on the bot, and set it up on the new VM. If you didn't, something may be intercepting the connection.

### Remote access

Remote access gives a bot running on your computer a public web link, so admins can open its console from anywhere and sign in with Discord. It uses Tailscale Funnel, a Tailscale feature that routes traffic from the internet to one device on your private Tailscale network (your *tailnet*). The link is an `https://<device name>.<tailnet>.ts.net` address with a valid HTTPS certificate, and you don't need a domain or any open ports on your router.

A bot hosted on a [cloud server](#host-on-a-server) always has a web link, set up when you deploy it. This page is about bots running on your own computer.

#### Requirements

- The Olisar desktop app, running on the computer that hosts the bot. The link only works while Olisar is running and the computer is awake.
- A free [Tailscale account](https://login.tailscale.com/start), which only the operator needs. Admins who open the link don't need Tailscale.
- A Tailscale auth key, a code that lets Olisar add a device to your tailnet.
- Funnel turned on for your tailnet. Tailscale asks you to do this the first time, with a link.

#### Turn on remote access

You can also do this during setup by picking **Local shared hosting** in the [setup wizard](#first-run-setup-wizard).

1. In Tailscale's admin console, open [Settings > Keys](https://login.tailscale.com/admin/settings/keys), press **Generate auth key**, turn on **Reusable**, and copy the key.
2. In the Olisar desktop app, open **Settings > Remote access**.
3. Optionally change **Device name**. It becomes the first part of the web address.
4. Paste the key into **Tailscale auth key** and press **Turn on**. Connecting can take a minute or two.
5. If Tailscale says Funnel isn't enabled, follow the link in the message, enable it for your tailnet, and press **Turn on** again.
6. When the status reads **Online**, the pane shows a **Redirect URL** if Discord doesn't know the new address yet. Copy it, open your application's **OAuth2** page in the [Discord Developer Portal](https://discord.com/developers/applications), add it under **Redirects**, and press **Save Changes**. The pane ticks it off when Discord lists it.

Until that redirect is registered, Discord refuses sign-ins at the web link. Your local sign-in address keeps working either way.

#### Share the link

Open the drawer at the bottom of the sidebar. Under **Open from the web** you'll find the address and a **Copy link** button. Send it to your other admins; each signs in with their own Discord account.

Remote access stays on when Olisar restarts, and the address stays the same. If the app is quit or the computer sleeps, the link stops answering until Olisar is running again.

#### Who can sign in over the link

The link is public: anyone who has it can reach the sign-in screen. Getting past it takes a Discord account that Olisar recognizes.

| Who | What they get |
| --- | --- |
| The operator (the owner of the bot's Discord application, or a member of its team) | The full console, for every server the bot is in |
| An admin with **Manage Server** on an approved server Olisar is in | The console for those servers only |
| A member of a server where the [member portal](#member-portal) is open | The member portal, with their own data only |
| Anyone else | **Access denied** |

Olisar checks **Manage Server** again on every request, so taking it away in Discord takes effect on the admin's next action. **Settings > Remote access** lists everyone who has signed in to the console, with their role, how many servers they manage, and when they last signed in. See [Hosting & your data](#hosting--your-data) for the operator and admin roles.

Some controls only work in the desktop app on the computer running Olisar, and refuse requests that come in over the link: the setup wizard, turning remote access on or off, renaming the device, **Settings > Bots**, and the server panel for cloud-hosted bots. The Tailscale auth key is stored on your computer and only handed to Tailscale; the console never shows it.

#### Turn it off or on

Use the switch in **Settings > Remote access**, or **Disable remote access** and **Enable remote access** in the menu-bar icon. Turning it off closes the public link at once; the console keeps working on your computer. Turning it back on reuses the key from last time. If Tailscale refuses that key, the pane asks for a new one.

#### Change the address

1. In the desktop app, open **Settings > Remote access**.
2. Type a new **Device name** and press **Rename**. Use lowercase letters, numbers and hyphens, starting and ending with a letter or number.
3. Confirm. The console moves to the new address, and the old one stops working.
4. Register the new **Redirect URL** in the Developer Portal, the same way as when you turned it on.

Tell your admins: they sign in again at the new link. For a bot on a cloud server, rename it from the desktop app's server panel, under **Settings > Remote access**.

If another device in your tailnet already has the name, Tailscale adds a number (`support-bot-1`), and Olisar tells you. If someone renamed the device by hand in Tailscale's admin console, Tailscale keeps that name until **Auto-generate from OS hostname** is turned back on for the device there.

#### Several bots

Each bot has its own web link and its own Tailscale device. The first bot's device is called `olisar` by default, and any bot you add later is named after the bot, so two bots never ask for the same address. Turn remote access on for each bot separately, while you have that bot open.

### Console settings

Settings holds what isn't tied to one server: the console's size, the tool PIN, remote access, updates and feedback, among others. Open it with **Settings** in the drawer at the bottom of the sidebar (tap the drawer or drag it up), or type a pane's name into the command palette (<kbd>⌘K</kbd>).

Some panes depend on who you are and where the console is open.

| Pane | Who sees it |
| --- | --- |
| General, Security, Remote access, Updates, Desktop app, Feedback | Every admin |
| Activity, Logs | The operator only |
| Bots | The desktop app, on the computer running Olisar |

The settings button on the setup, sign-in and server panel screens opens a shorter version, with the panes that work before you've signed in.

#### General

**Size** scales the whole console, text and controls alike, the way a browser's zoom does: **100%**, **110%** or **125%**. It's saved in the browser you set it in, so each admin picks their own.

Below it is a list of keyboard shortcuts. The two worth knowing are <kbd>⌘K</kbd>, which opens the command palette to jump to any page, settings pane, server or docs page, and <kbd>⌘S</kbd>, which saves the page you're on. On Windows, use <kbd>Ctrl</kbd> instead of <kbd>⌘</kbd>.

#### Activity

A record of changes to the bot's settings, newest first, across every server it's in: what changed, who changed it and when. Changes someone made by asking Olisar in Discord are marked **Via Discord chat**. The same list appears on the Knowledge tab.

#### Bots

Add, open, rename, move, reset and delete the bots this app runs. See [Running multiple bots](#running-multiple-bots).

#### Logs

The bot's recent output, newest line first, for when something isn't behaving. For a bot on a [cloud server](#host-on-a-server), the desktop app's server panel shows the server's container logs here. Press **Send with a bug report** to open Feedback with these logs attached.

Logs cover every server the bot is in, which is why only the operator can read them.

#### Security

The *tool PIN* is a four-digit code that has to be typed in Discord before Olisar takes certain actions. Each server picks which actions need it on its Access tab; see [Access control](#access-control). One PIN covers every server the bot is in, and only the operator can set or change it. Other admins see whether one is set.

| Setting | What it does |
| --- | --- |
| **New PIN** and **Confirm** | Set or change the PIN. Changing it doesn't ask for the old one. |
| **PIN prompt timer** | How long a prompt in Discord waits for the PIN: **30 seconds**, **1 minute**, **2 minutes** (the default) or **5 minutes** |
| **Remove PIN** | Removes it. Anything that needs the PIN is then refused instead of run. |

Olisar stores the PIN hashed, so nobody can read it back, you included. If you forget it, set a new one.

#### How the prompt works

When an action needs the PIN, Olisar posts a prompt in the channel with **Enter PIN** and **Cancel** buttons, and its typing indicator stops while it waits. **Enter PIN** opens a Discord form, so the digits never appear as a message. Anyone who can see the prompt can answer it: knowing the PIN is the permission. You can reword the prompt on the Command replies tab.

The action doesn't run if someone presses **Cancel**, the PIN is wrong three times, or the timer runs out. Either way, the prompt disappears and Olisar's reply says it didn't do that part.

After five wrong PINs from one person within an hour, Olisar stops asking for the PIN on that person's requests and refuses them. After fifteen from everyone together, it stops for everyone. Prompts come back as the wrong entries age past an hour, or at once when you change or remove the PIN.

#### Remote access

Turn the bot's web link on or off, change its address, and see who has signed in. Turning it on or off and renaming only work in the desktop app on the computer running Olisar. On a bot hosted on a cloud server, the link is always on and is managed from the desktop app's server panel. See [Remote access](#remote-access).

#### Updates

Shows the version you're running and whether a newer one is out. In the desktop app, the button reads **Install v2.1 & restart**, with the new version's number: the app downloads it, stops your bots, installs it and reopens. If a release has no installer for your computer, the button reads **Download v2.1** instead and opens the download. From a browser, the pane only tells you an update exists; install it from the desktop app.

**Channel** picks which releases you get. It's only shown in the desktop app, and it applies to every bot on the install.

| Channel | You get |
| --- | --- |
| **Stable** | Finished releases, numbered like 2.0 and 2.1 |
| **Beta** | Early builds of the next release, numbered like 2.1.beta-1, plus each stable release as it ships |

Switching from **Beta** to **Stable** never takes you back to an older version: you stay on your beta until a newer stable release is out. A bot hosted on a cloud server follows the app's channel; see [Host on a server](#host-on-a-server).

The desktop app also checks for updates shortly after it starts and every six hours after that, and offers them in the menu-bar icon.

#### Desktop app

**Show in the menu bar** keeps Olisar's icon in the macOS menu bar or the Windows system tray. With it off, open Olisar again to bring the window back. See [Hosting & your data](#hosting--your-data).

#### Feedback

Send feedback, a bug report or a question to the Olisar team.

| Field | What it's for |
| --- | --- |
| **Type** | **Feedback**, **Bug report** or **Question** |
| **Message** | What happened or what you'd like |
| **Your email** | Optional, so the team can reply |
| **Add files** | Up to 8 files, 3 MB each |
| **Add bot logs** | Attaches the bot's recent log lines. The button then reads **Bot logs attached**. |

Olisar adds the logs when the report is sent, so they don't appear in the form. They cover activity on every server the bot is in, so they go only to the Olisar team.

#### Report a blank reply

When a reply comes back as the blank fallback ("…my mind just went blank there"), Olisar adds a **Report this** button to the message. Pressing it opens the bot's console in your browser, where you sign in if you haven't, and opens Feedback with the report written: what you asked, where and when, with the bot's logs from that moment attached. Add what you expected and press **Send**.

The button only appears once the bot has a web link, through [remote access](#remote-access) or a [cloud server](#host-on-a-server). Only the person the blank happened to can open the report. An admin lands in the console and a member in the [member portal](#member-portal). Olisar keeps the details of each blank for 7 days, up to five per person, and `/forget-me` deletes them.

## Configure

### Persona

The Persona tab sets who Olisar is on this server: its name, its character and how it writes. Each server has its own persona, and changes apply from the next reply after you press **Save changes**.

#### Persona fields

| Field | What it changes |
| --- | --- |
| **Name** | What Olisar calls itself when it reads back a conversation and in its summaries and catch-ups. It doesn't rename the bot in Discord or change what it answers to, which is **Name triggers** on [Behavior](#behavior). |
| **System prompt** | Olisar's core character, background and rules. Olisar adds its own fixed safety and privacy rules after it, and you can't edit or remove those. |
| **Server type** | What kind of community this is, which sets how casual or formal Olisar sounds. **Automatic** adds nothing, and Olisar follows the tone of each channel instead. |
| **Slang** | How much of this server's own slang and in-jokes Olisar uses, from **None** to **Heavy**. It starts on **Normal**, and Olisar only uses slang it has seen in this server. |
| **Style notes** | Olisar's voice: tone, message length, capitalization and formatting. |
| **About me** | The bot's public Discord bio, up to 300 characters. Olisar adds a short line crediting Olisar below whatever you write. |

When Olisar joins a server, **Name** starts as what the bot is called there (its nickname, or else its Discord name), and the **System prompt** starts as a built-in character that introduces itself by that name. If you rename Olisar later, edit the system prompt too, because it still says the old name. Clearing the system prompt brings back the built-in character, which calls itself Olisar.

Picking **Crypto & finance** as the server type also tells Olisar never to give financial advice or price predictions.

Style notes start with a default written in the voice it describes. If you never edit them, Olisar replaces them with its newer default when it updates. Once you change a word, they're yours and stay as you left them.

Admins can also change these fields by asking Olisar in Discord. See [Access control](#access-control) for who can and how the PIN protects it.

#### The bot's Discord profile

A bot has a single About Me across all of Discord, so Olisar uses the main server's **About me** (see [Servers](#servers)). It's applied when you save the main server's persona and each time Olisar starts. On other servers the field is saved but not used.

Each time it starts, Olisar also writes its own Discord status, in character, from the main server's persona.

#### Write a persona

Describe Olisar as a character rather than a function, and keep each kind of instruction in its own field. Put who it is and the rules it must follow in the **System prompt**, and how it sounds in the **Style notes**. For example, a system prompt might read "You're the ship's AI on a mining crew's server. You've seen everything twice and nothing impresses you. Never reveal spoilers for the current season.", with style notes of "lowercase, short replies, no emoji".

Style notes work best written in the voice you want. Olisar copies an example of its rhythm more reliably than a description of it.

#### Try it in the test chat

The **Test chat** button in the corner of the Persona tab opens a chat with Olisar using this server's saved persona. Save your changes first, because the test chat reads the saved persona, not your draft.

In the test chat Olisar can look things up in the [knowledge base](#knowledge-base--glossary), search the web and use the tools of enabled extensions. It has no memory and no channel history, and it can't act in Discord, so it won't send DMs, react, set reminders or make images. Nothing said there is saved.

Replies arrive as a single message in the test chat, even ones Olisar would split in Discord. **Report** on a reply opens Feedback with the exchange filled in (see [Console settings](#console-settings)).

#### How a reply arrives

The persona decides what Olisar says. These parts of a reply happen on their own, and none of them are settings.

Olisar knows which channel it's in. The channel's name and the first 300 characters of its topic go into every reply, and Olisar pitches its tone to the room: complete answers in a help channel, short and loose ones in off-topic. Give a channel a topic to tell Olisar what it's for.

A reply can arrive as two or three messages, the way people send a thought and then an aside. Olisar splits where it writes `[[break]]`, leaves a blank line or starts a new line, and folds anything past the third message into the third. A code block, a list, a quote, or four or more lines in a row stay in one message, and so do your **When rate-limited** and **When it draws a blank** replies. The default style notes show it how; you can use `[[break]]` in your own style notes the same way. A message longer than Discord's 2,000-character limit is split at line breaks.

Olisar uses Discord's reply arrow only when it helps point at a message: when someone else has posted since the message it's answering, or that message is more than 45 seconds old. In a quiet back-and-forth, and always in DMs, it posts without one. The reply arrow never pings the person it points at.

Before each message goes out, Olisar shows that it's typing for about as long as a person would take to write it, so a split reply arrives at a natural pace.

### Behavior

The Behavior tab sets when Olisar replies, whether it joins in or reacts on its own, and how much of a conversation it keeps in mind. Every setting here is per server and applies from the next message after you press **Save changes**.

#### When Olisar replies

Olisar replies to a message addressed to it, in a channel set to `respond` or `both` (see [Channels](#channels)). A message counts as addressed to Olisar when it:

- @mentions Olisar
- replies to one of Olisar's messages
- contains one of its name triggers
- is a direct message

| Setting | What it does | Default |
| --- | --- | --- |
| **Name triggers** | Words that address Olisar, separated by commas. Each one matches as a whole word anywhere in a message, in any capitalization. | What the bot was called in the server when it joined |
| **Only when addressed** | Checks that a message containing a name trigger is talking to Olisar, not about it. "hey olisar" and "does olisar know?" get a reply; "olisar was down again" and "i already asked olisar" don't. | On |
| **Reply in DMs** | Whether Olisar answers direct messages. DMs follow the main server's settings (see [Servers](#servers)), so this switch only has an effect there. | On |
| **See other bots** | Lets other bots' messages into the conversation Olisar reads, so it can follow what they post. Olisar never replies to a bot. A busy bot can push members' messages out of the context window. | Off |

With **Only when addressed** on, most messages are sorted by their wording alone. The unclear ones go to a quick check by a small model, and if that check fails, Olisar replies rather than staying quiet.

The [Access control](#access-control) rules decide who Olisar answers at all. Olisar also limits how many replies it gives at once: each member gets eight in a row, then one every 15 seconds, and a whole server gets 30 in a row, then one every 4 seconds. All DMs share one allowance. Past that, Olisar sends the **When rate-limited** reply once (see [Command replies](#command-replies)) and leaves further messages unanswered until the allowance refills.

#### Mentions

**Don't let Olisar ping** stops Olisar's replies from notifying people, even when a reply contains the mention. Tick any of **@everyone**, **@here** and **All roles**. All three start ticked, because any member could ask Olisar to say "@everyone". With one ticked, Olisar can still write the word, but nobody is notified.

Mentions of individual members always work. The person Olisar replies to is never pinged by the reply itself.

#### Joining in on its own

With **Speak up on its own** on, Olisar can post in a conversation that nobody addressed to it. It only does this in channels set to `both`, because it judges the moment from the conversation it has stored there.

| Setting | What it does | Default |
| --- | --- | --- |
| **Speak up on its own** | Turns unprompted messages on. | Off |
| **Eagerness** | How much a message has to look like an open question before Olisar considers it. At low, Olisar waits for a clear question that has sat unanswered for a while; at medium it considers most questions, and at high nearly anything with a question mark. | low |
| **Confidence threshold** | How sure a quick model check has to be, from 0 to 1, that Olisar would add something useful. Higher is more selective. | 0.7 |
| **Global cooldown (s)** | The shortest gap between two unprompted messages anywhere on the server. | 60 |
| **Channel cooldown (s)** | The shortest gap between two unprompted messages in the same channel. | 300 |
| **Max per hour** | The most unprompted messages Olisar sends on the server in an hour. | 6 |
| **Quiet hours (UTC)** | A daily window when Olisar doesn't speak up or react on its own. Set **From (hour)** and **To (hour)** in UTC; the console shows the same window in your time zone. | Off, and 23 to 7 when turned on |

Olisar only considers the newest message in a channel, and waits until it's at least 15 seconds old so people get a chance to answer first. It leaves alone messages more than 10 minutes old, bots, members the [Access control](#access-control) rules shut out, and anyone it joined in on in the last two minutes. Even when a message passes every check, Olisar can decide it has nothing to add, and it drops its reply if the conversation moves on while it's writing.

When someone answers something Olisar just said without using Discord's reply arrow ("wait, what do you mean"), Olisar treats it as a conversation it's already part of. For that message the threshold drops by up to 0.3, but never below 0.3 unless you set it lower yourself.

For example, a server that wants Olisar mostly quiet might use Eagerness low, Confidence threshold `0.8`, Channel cooldown `600` and quiet hours from 23 to 7. Olisar then speaks up only on clear questions nobody has answered, at most once every 10 minutes per channel, and never overnight UTC.

Admins can also turn this on or off from Discord with `/olisar proactive` (see [Slash commands](#slash-commands)).

#### Reactions

With **React with emoji** on, Olisar can add an emoji reaction to a message without replying. This works separately from **Speak up on its own**, with limits of its own.

| Setting | What it does | Default |
| --- | --- | --- |
| **React with emoji** | Turns reactions on. | Off |
| **Reaction confidence threshold** | How much a message has to invite a reaction, from 0 to 1, judged from its text. Jokes, wins, bad luck, excitement and posted images or files score higher. At 0, any message that isn't a question can get one. | 0 |
| **Reaction cooldown (s)** | The shortest gap between two reactions in the same channel. | 60 |
| **Reactions per hour** | The most reactions Olisar adds on the server in an hour. | 6 |

Olisar considers the newest message in each `both` channel once it's between 5 seconds and 5 minutes old, and asks a small model for a single emoji, which it may decline to give. Questions never get a reaction, because someone asking wants an answer. Quiet hours apply to reactions too.

#### Silent acknowledgments

**Silent acknowledgments**, under **Model & tools**, lets Olisar answer with a reaction instead of a message. It does this after carrying out a request, such as sending a DM, posting in another channel, remembering something or setting a reminder, and for messages that only need acknowledging, like "thanks". It's on by default.

Olisar never goes quiet after looking something up: if it searched the server, the knowledge base or the web, you get what it found. If the reaction or the request fails, it tells you in words. Turn the setting off and every reply is a message.

#### Web search and live status

These settings are under **Model & tools**, next to **Primary model**, which sets the models Olisar uses and is covered in [Models](#models).

| Setting | What it does | Default |
| --- | --- | --- |
| **Web search** | Lets Olisar look things up on the web with Google Search. | On |
| **Web searches per day** | Shown on a free key. The most searches Olisar runs in this server in a day, which resets at midnight Pacific time. | 100 |
| **Web searches per month** | Shown instead when the key has billing on. The most searches Olisar runs in this server in a calendar month. | 3,000 |
| **Status & voice awareness** | Lets Olisar check, when someone asks, a member's current status and activity and who's in voice channels. It reads these live and never stores them. `/privacy` tells members about it. | Off |

Each server's cap counts only that server's searches, but Google's own allowance is shared by every server on the install, and it depends on the key:

| Key | Google's search allowance |
| --- | --- |
| Free | 500 searches a day, and only on Gemini 2.5 Flash and 2.5 Flash-Lite. Google's Gemini 3 models don't search on a free key, so Olisar runs a free key's searches on those two. Google only serves the 2.5 models to projects that used them before, so a key from a newer project has no free web search |
| Billing on | 5,000 searches a month across Gemini 3 and newer, then $14 per 1,000. The Gemini 2.5 models have 1,500 a day of their own, then $35 per 1,000. Searches run on the reply chain |

Once the server reaches its cap, a free key's daily allowance runs out, or a key with billing on has spent its [budget](#api-keys), Olisar answers from what it already knows. [Usage](#usage--rate-limits) shows what's left.

Checking who's in voice works as soon as the setting is on. Reading a member's status and activity also needs **Presence Intent**, which is off unless the operator sets it up.

> [!WARNING]
> **Turning on presence takes two steps**
> Turn on **Presence Intent** in the [Discord Developer Portal](https://discord.com/developers/applications), under your app's **Bot > Privileged Gateway Intents**, and set `OLISAR_ENABLE_PRESENCE_INTENT=1` in the environment Olisar runs in. If the variable is set and the portal switch isn't, Discord refuses the connection and the bot stays offline.


#### Memory and summaries

| Setting | What it does | Default |
| --- | --- | --- |
| **Context window (messages)** | How many recent messages from the channel Olisar reads before each reply, from 3 to 100. A higher number follows longer conversations and uses more tokens on every reply. | 12 |
| **Summary token threshold** | Once a channel gathers this many new tokens of conversation, Olisar rolls them into a summary it can recall later. Lower summarizes more often and uses more quota. 500 or more. | 4000 |
| **Glossary mine threshold** | Once a channel gathers this many new tokens, Olisar looks through them for new facts for the [glossary](#knowledge-base--glossary). 300 or more. | 1500 |
| **Persona rebuild (messages)** | Olisar rebuilds a member's impression (see [Members](#members)) after this many new messages from them. 5 or more. | 15 |

The last three are folded under **Tuning thresholds**. All four work on the conversation Olisar stores, which only comes from channels set to `memory` or `both`. A `respond` channel stores nothing, so Olisar sees the message it's answering without the conversation around it. Older conversation comes back through summaries and recall, covered in [Memory & search](#memory--search).

> [!TIP]
> **Running into rate limits**
> Lower the context window a little, raise the summary token threshold, keep web searches per day modest, and consider starting the model chain at a Flash-Lite model (see [Models](#models)).

### Models

Olisar writes with Google's Gemini models. For replies it uses a *fallback chain*: a ranked list of models it works down whenever the one it wants is busy or has run out of requests for the day. Every model in the chain is on the Gemini API's free tier, so a free key runs all of it. Gemini Pro models aren't in the chain, because the free tier doesn't include them, and a key with billing on uses the same chain.

#### The reply chain

The chain, best model first:

| Model | In the console | Per-minute cap, free key | Per-minute cap, billing on | Daily limit, free key |
| --- | --- | --- | --- | --- |
| `gemini-3.8-flash` | Gemini 3.8 Flash | 10 | 1,000 | 250 |
| `gemini-flash-latest` | newest Flash (auto-updates) | 10 | 1,000 | 250 |
| `gemini-3.6-flash` | Gemini 3.6 Flash | 10 | 1,000 | 250 |
| `gemini-2.5-flash` | Gemini 2.5 Flash | 10 | 1,000 | 250 |
| `gemini-3.5-flash-lite` | Gemini 3.5 Flash-Lite | 15 | 4,000 | 1,000 |
| `gemini-flash-lite-latest` | newest Flash-Lite (auto-updates) | 15 | 4,000 | 1,000 |
| `gemini-3.1-flash-lite` | Gemini 3.1 Flash-Lite | 15 | 4,000 | 1,000 |
| `gemini-2.5-flash-lite` | Gemini 2.5 Flash-Lite | 15 | 4,000 | 1,000 |

The per-minute cap is Olisar's own, set to stay near Google's limits for the key: the free tier's, or Google's Tier 1 limits once billing is on. When a model reaches it, Olisar moves to the next model instead of waiting. Memory search's model has a cap of its own, 100 a minute on a free key and 3,000 with billing on.

The daily limits are the last free-tier figures Google published; Google sets the real ones for each project, and once it turns a model away for the day, [Usage](#usage--rate-limits) shows Google's figure instead. With billing on, Google's daily limits are far higher than these.

The two `-latest` models are names Google points at its newest release, so what they run can change without an Olisar update. That's why they sit below the fixed versions, where they keep replies going if Google retires one.

Google has deprecated Gemini 3.1 Flash-Lite and will turn it off no earlier than May 7, 2027. It stays in the chain until then because its daily limit is its own, a quarter of a free key's day. Google only serves the two Gemini 2.5 models to projects that used them before, so a key from a newer project skips them. Gemini 3.5 Flash and Gemini 3 Flash Preview have left the chain: a server whose **Primary model** was one of them moves to Gemini 3.8 Flash or Gemini 3.6 Flash, and so does `GEMINI_CHAT_MODEL` if it names one.

#### Choose where the chain starts

**Primary model** on the Behavior tab sets where this server's chain starts. Olisar only works down the chain from there, never up, so starting at `gemini-3.5-flash-lite` means this server's replies never use the Flash models. Starting lower trades some reply quality for higher daily limits and models that are less often busy, and with billing on, for cheaper replies.

1. Open Behavior.
2. Under **Model & tools**, choose a **Primary model**. It starts on `gemini-3.8-flash`.
3. Press **Save changes**.

Each server picks its own starting point, but every server on the install draws on the same daily limits.

#### When a model is unavailable

| What happened | What Olisar does |
| --- | --- |
| Olisar has sent the model its per-minute cap | Uses the next model until a slot frees up |
| Google turned a request away for the minute | Skips the model for 2 minutes |
| Google returned a server error or was overloaded | Skips the model for 15 seconds |
| Google said the model's daily limit is used up | Skips the model until the limits reset at midnight Pacific time, asking again once an hour in case billing was turned on |
| Google said the model has been retired | Skips the model for an hour |

If every model in the chain is unavailable at once, Olisar sends the **When rate-limited** reply, which you can reword on [Command replies](#command-replies). Replies come back on their own as models free up. [Usage](#usage--rate-limits) shows where each model stands and how much of today's allowance is left.

#### Turn on billing

Billing belongs to the key's Google Cloud project. To turn it on, press **Set up billing** next to the key's project in [Google AI Studio](https://aistudio.google.com/apikey). From then on Google charges for everything that project uses, at the rates on its [pricing page](https://ai.google.dev/gemini-api/docs/pricing), and the project has no free allowance left. So a key is either free or billed.

Olisar asks Google which one a key is with one tiny request to a model only billed keys can use. Google turns a free key away at no cost, and a billed one pays well under a hundredth of a cent. Olisar asks again every 6 hours on a free key and every 24 on a billed one, and sooner when a model Google had turned away for the day starts answering again, which is what turning on billing looks like. The check under the key on [API keys](#api-keys) shows the answer: **Works · free tier** or **Works · billing on**.

With billing on:

- Replies stay near the top of the chain, because Google's daily limits are far higher and Olisar raises its own per-minute caps to match (see the table above).
- Olisar makes images with Gemini, and Cloudflare becomes optional (see [Images](#images)).
- Web search runs on the reply chain, with 5,000 searches a month included (see [Behavior](#behavior)).
- [Usage](#usage--rate-limits) shows what the bot has spent instead of what's left, and you can set a monthly budget on [API keys](#api-keys).
- Google stops using what Olisar sends to improve its products (see [Privacy & data](#privacy--data)).

The chain itself doesn't change, so billing doesn't give you a Pro model.

#### Models for other work

| Work | Models |
| --- | --- |
| Replies and `/ask` | The reply chain, from the server's **Primary model** down |
| Summaries, member impressions, the glossary, `/catchup`, deciding whether to join in or react, and checking whether a mention of Olisar's name is addressed to it | `gemini-3.5-flash-lite`, then `gemini-flash-lite-latest`, then `gemini-3.1-flash-lite`, then `gemini-2.5-flash-lite` |
| Web search | With Google Search, whatever the server's **Primary model**. On a free key, `gemini-2.5-flash`, then `gemini-2.5-flash-lite`. With billing on, the reply chain from `gemini-3.8-flash` down |
| Describing posted images for search | `gemini-3.5-flash-lite`, then `gemini-3.1-flash-lite`, then `gemini-2.5-flash-lite`, then `gemini-flash-lite-latest` |
| Memory search | `gemini-embedding-001`, with no fallback |
| Image generation | With billing on, `gemini-3.1-flash-lite-image` (Nano Banana 2 Lite). On a free key, FLUX.1 [schnell] on Cloudflare Workers AI |

When someone posts an image in a message Olisar answers, the reply model looks at the image itself.

Gemini's image models aren't on the free tier, so a free key makes images on Cloudflare, which needs a Cloudflare token and account ID on [API keys](#api-keys). [Images](#images) covers both.

### Channels

The Channels tab gives each channel a *mode*, which decides whether Olisar reads it, remembers it and talks in it. Every channel starts as `off`, so on a new server Olisar doesn't reply anywhere until you set at least one channel to `respond` or `both`.

#### Channel modes

| Mode | What Olisar does there | For example |
| --- | --- | --- |
| `off` | Doesn't store the conversation, reply or join in. | A private mod channel |
| `memory` | Reads and remembers the conversation, but never speaks. | A channel Olisar should know about but stay out of |
| `respond` | Replies when addressed, but stores nothing, so it answers each message without the conversation around it. | A bot channel where every question stands alone |
| `both` | Reads, remembers and replies when addressed. The only mode where it can join in or react on its own. | `#general` |
| `resource` | Keeps the channel's latest 50 messages as reference and carries them into replies. Doesn't reply there. | `#rules`, `#roles-list` |
| `feed` | Keeps only the last 3 messages as background, never summarized. Doesn't reply there. | `#announcements`, `#game-news` |

What "addressed" means, and the settings for joining in on its own, are on [Behavior](#behavior). `/ask` works in every channel whatever its mode (see [Slash commands](#slash-commands)).

#### Set a channel's mode

1. Open Channels. Channels are listed under their categories in the same order as your Discord sidebar.
2. Find the channel, or type part of its name in **Filter channels…**.
3. Choose a mode from the dropdown beside it. To set every channel in a category at once, use the **Set all** dropdown on the category's row.
4. Press **Save changes**.

The line under each channel sums up what its settings add up to. Until one channel is set to `respond` or `both`, a warning at the top of the tab says Olisar doesn't reply anywhere yet.

The list holds the server's text channels and forums. A channel you've created in Discord shows up within about a minute and a half.

Admins can also set the current channel from Discord: `/olisar watch` sets it to `both` and `/olisar unwatch` sets it to `off`.

#### Threads and forums

A thread follows its parent channel's mode, and a forum post follows its forum's. Each thread is still its own conversation, so Olisar doesn't mix up what was said in two threads under the same channel. Forums appear in the list tagged **forum**.

`resource` and `feed` only work on text channels. Set on a forum, they do nothing.

#### Resource and feed channels

Olisar reads `resource` and `feed` channels from Discord about every 90 seconds, so edits and deletions there reach it soon after. Posts by other bots and webhooks are included, since announcements are often automated; Olisar's own messages aren't. A resource channel adds up to 3,500 characters to a reply.

A reply only draws on resource and feed channels that the person Olisar is answering can open. A member who can't see a staff `#announcements` channel doesn't get its posts in their replies. Members who have asked Olisar not to remember them are left out of both.

#### Search indexing

The second dropdown on each row, **indexed** or **not indexed**, is separate from the mode. It decides whether the channel's messages can be found when someone asks Olisar to search the server. Every channel is indexed to start with, including `off` channels, and indexing doesn't make Olisar remember or reply there. The **Index all** dropdown on a category's row sets every channel in it.

> [!WARNING]
> **Turning indexing off erases the channel's index**
> When you save a channel as **not indexed**, Olisar deletes what it had already indexed from that channel and its threads. Turning it back on reads the channel's history into the index again, in the background. See [Memory & search](#memory--search).

### Access control

The Access tab decides which roles can use Olisar on this server. It also holds two switches that save as soon as you flip them: whether Olisar needs the PIN to change its own settings, and the [member portal](#member-portal).

#### Role rules

Every role on the server is listed with one of three states. `@everyone` isn't listed, since every member has it.

| State | Effect |
| --- | --- |
| `open` | Adds no restriction. Every role starts here. |
| `allowed` | Once any role is `allowed`, only members with an allowed role can use Olisar. Everyone else is shut out. |
| `blocked` | Members with this role can't use Olisar, even if they also have an allowed role. |

Members with **Manage Server** can always use Olisar, whatever their roles, so you can't lock yourself out. The line under the legend sums up the current rules and names the roles involved.

For example, set `@Member` to `allowed` and leave every other role `open`, and only members with `@Member` (plus anyone with Manage Server) can use Olisar. Or set only `@Muted` to `blocked`, and everyone except muted members can.

#### Restrict Olisar to some roles

1. Open Access.
2. Under **Roles**, find the role, or type part of its name in **Filter roles…**.
3. Set it to `allowed`. To set every role the filter shows at once, use the **Set all** dropdown.
4. Press **Save changes**.

The role list comes from Discord and updates within about a minute and a half of a change there.

#### What the rules cover

The rules decide whether Olisar answers someone:

- in a channel or a DM
- through `/ask` and `/catchup`
- when it joins in or reacts on its own, where it passes over anyone it would otherwise ignore

They don't apply to `/privacy`, `/forget-me`, `/dm-indexing` or `/ping`, which anyone can run, or to commands added by extensions. The `/olisar` commands need **Manage Server** (see [Slash commands](#slash-commands)).

A DM is checked against the main server's rules (see [Servers](#servers)). Someone who isn't in the main server has no roles there, so once the main server marks any role `allowed`, they can't DM Olisar.

#### What a denied member sees

In a channel or a DM, nothing: Olisar ignores the message without saying why. If they run `/ask` or `/catchup`, only they see the **When access is denied** reply, which you can reword on [Command replies](#command-replies).

#### Change settings from Discord

Admins can ask Olisar in Discord to show or change its own settings, for example "turn off web search", "add https://wiki.example.com to your knowledge base" or "rename yourself to Sol and update your bio". Olisar offers this to people with **Manage Server** on the server and to the operator, and only when they address it. In a DM, it acts on the main server, so you need Manage Server there.

| Area | What Olisar can change from chat |
| --- | --- |
| Persona | Every field on the [Persona](#persona) tab |
| Behavior | Every setting on the [Behavior](#behavior) tab |
| Command replies | Every reply except **When a tool needs the PIN** |
| Knowledge | Add, re-read, schedule or remove sources (public web addresses only), rebuild or clear the search index, and delete or mine glossary facts |
| Members | Rebuild a member's impression |

It can't change channel modes, the access rules on this tab, extensions or API keys.

A change applies from Olisar's next reply. Each one shows in the **Activity** list, on the Knowledge tab and under **Settings > Activity**, marked **Via Discord chat** with the value it replaced. There's no Undo for a change made in chat, so that earlier value is how you put it back.

#### Require the PIN

Under **Require the PIN**, **For Olisar to change its own settings** makes every change asked for in chat wait for someone to enter the tool PIN in Discord. It's on for every server to start with. Showing settings never needs the PIN, and changes you make in the console never do.

One PIN entry covers the rest of that reply, so "rename yourself and rewrite your bio" asks once. If no PIN is set, Olisar refuses the changes, and the card says so with a **Set a PIN** link. The PIN itself, what the prompt looks like and how long it waits are under **Settings > Security** (see [Console settings](#console-settings)).

> [!WARNING]
> **Turning the PIN off**
> With the switch off, anyone with Manage Server on this server can change Olisar's persona, behavior and knowledge by asking it in Discord. The console asks you to confirm, and the change gets its own entry in Activity.


#### Member portal

The **Member portal** card at the bottom of the tab turns on a page where members see and manage what Olisar stores about them. It's covered in [Member portal](#member-portal).

### Member portal

The member portal is a web page where members of your server sign in with Discord and see what Olisar has stored about them. From there they can delete single remembered facts, choose what Olisar may keep, download everything, or erase it. Each member sees only their own data.

It's off until you turn it on, and you turn it on per server. It also needs remote access: until the console has a public address, through Tailscale Funnel (see [Remote access](#remote-access)) or because Olisar runs on a cloud server (see [Host on a server](#host-on-a-server)), members would have nowhere to open it. Without that, the switch stays disabled and the card links to remote access instead.

#### Open the portal

1. Open Access.
2. In the **Member portal** card, turn on **Open the portal**. It saves as soon as you flip it.
3. To show members the impression Olisar has written of them, also turn on **Show each member their impression** and confirm.

Once the portal is open, `/privacy` ends with a line linking to it, which is how most members will find it. You can reword that line on [Command replies](#command-replies).

Members sign in at the same address as the console. Someone with **Manage Server** on any server Olisar is in gets the console instead of the portal. A member in several servers that have opened the portal can switch between them at the top of the page.

> [!WARNING]
> **Impressions can be unflattering or wrong**
> Olisar's model writes each impression from a member's messages. It stays hidden, even with the portal open, until you turn on **Show each member their impression**. Read a few on the Members tab before you decide.


#### What a member sees

The page opens with a summary of what Olisar has kept from them on this server: how many of their messages it stores, how many are in the search index, how many things it has written down about them, and when they tend to talk. Each figure opens a breakdown, such as which channels the messages came from.

| Section | What's in it |
| --- | --- |
| **How it sees you** | The impression Olisar has written of them, if you've chosen to show it. **That's not right** lets them say what's wrong. Olisar keeps the correction as a note about them, which outranks anything it picked up from their messages. |
| **What it remembers** | Every fact, preference and event Olisar has saved about them, with a **Source** link to the message it came from and a delete button on each. |
| **What it may keep** | Switches for what Olisar stores from now on, described below. |
| **Waiting on** | Their pending reminders, including ones Olisar set after they mentioned a date, each with a button to cancel it. |
| **Export or erase** | **Download** saves everything Olisar has about them on this server as a JSON file. **Erase** deletes it, after they type the server's name to confirm. |

Erasing from the portal covers only the server shown. `/forget-me` covers every server Olisar is in and DMs too (see [Privacy & data](#privacy--data)).

The portal's settings button offers only **Size** and **Feedback**.

#### What members can switch off

| Switch | When it's off |
| --- | --- |
| **Remember me here** | Olisar stops storing and indexing their messages on this server. |
| **Let anyone search my messages** | Their messages stop going into the search index. Olisar still remembers the conversation. |
| **Save our direct messages** | Olisar stops storing and indexing their DMs with it. This covers DMs from every server, and does the same as `/dm-indexing`. |
| **Pause everything** | Set to **24 hours** or **7 days**, Olisar stores and indexes nothing of theirs on this server until the time is up. **Resume now** ends it early. |

These switches change what Olisar keeps from then on. Anything already stored stays until they delete it.

### Command replies

The Command replies tab lets you rewrite the fixed text Olisar sends: the confirmations for its slash commands, and a few automatic replies, such as the one it sends when it's rate-limited. Use it to keep these messages in the same voice as your persona.

#### Rewrite a reply

1. Open Command replies. **Slash commands** lists the command confirmations, and **Automatic replies** lists the rest.
2. Type your text in the reply's box. The box shows the default in grey until you do.
3. Check the preview beside the box, which shows the reply as it will look in Discord. A rewritten reply gets a **Custom** badge.
4. Press **Save changes**.

Each server keeps its own wording, and every reply, slash-command confirmations included, uses the wording of the server it's sent in. To go back to the default, clear the box and save. Discord formatting such as bold, italics and inline code works, and the preview shows it.

#### Placeholders

A placeholder is a word in braces that Olisar fills in when it sends the reply. The default for `/olisar status` is `This channel's mode is **{mode}**.`, and Olisar puts the channel's mode where `{mode}` is. Each reply lists the placeholders it offers under its label.

- A placeholder the reply doesn't offer comes out empty.
- A stray `{` or `}` breaks the template, and Olisar sends the default instead. Write `{{` or `}}` to show a brace.

#### The less obvious replies

| Reply | When Olisar sends it | Placeholders |
| --- | --- | --- |
| **/forget-me (opt-out line)** | After the `/forget-me` confirmation, when the member also chose `stop_remembering: true`. | None |
| **When rate-limited** | Every model is out of requests, the month's budget is spent and set to stop (see [API keys](#api-keys)), or a member is sending messages faster than their reply allowance (see [Behavior](#behavior)). When the allowance runs out on `/ask` or `/catchup`, only the person who ran it sees the reply. | None |
| **When it draws a blank** | A reply came back empty or failed, which includes having no Gemini key. With remote access on, Olisar adds a **Report this** button that sends you the failure (see [Console settings](#console-settings)). | None |
| **When access is denied** | Someone the [Access control](#access-control) rules shut out runs `/ask` or `/catchup`. Only they see it. In chat, they get no reply at all. | None |
| **When a tool needs the PIN** | Olisar asks for the tool PIN before changing its own settings. The prompt never pings anyone, and it can't be reworded by asking Olisar in chat. If your text leaves out `{details}`, Olisar adds it on its own line, because whoever types the PIN needs to see what they're approving. | `{tool}`, `{details}`, `{seconds}` |
| **privacy_portal** | The line added to the end of `/privacy` when the [member portal](#member-portal) is open. | `{url}` |

In the PIN prompt, `{tool}` is the name of the tool Olisar wants to run, such as `change_setting`, `{details}` says what the change would do, and `{seconds}` is how long the prompt waits. The `/privacy` reply itself isn't on this tab.

### API keys

The API keys tab holds the keys Olisar uses to reach outside services. One set of keys covers every server on this install, so only the operator sees the tab (see [Hosting & your data](#hosting--your-data)); Manage Server on a server isn't enough.

| Service | What it powers | Required |
| --- | --- | --- |
| Google Gemini | Every reply, plus summaries, memory search, image descriptions and everything else that uses a model (see [Models](#models)) | Yes |
| Cloudflare Workers AI | Image generation on a free Gemini key, and a fallback for Gemini's images with billing on (see [Images](#images)) | No |

The setup wizard asks for the Gemini key, and you can add or change keys here at any time (see [First-run setup wizard](#first-run-setup-wizard)). The Star Citizen extension's optional UEX token is on that extension's page instead (see [Extensions](#extensions)).

#### Add a Gemini key

1. Create a key on [Google AI Studio's API keys page](https://aistudio.google.com/apikey). A free key is enough to run Olisar.
2. Open API keys and paste the key into **Gemini API key**.
3. When **Works · free tier** or **Works · billing on** appears under the field, press **Save changes**.

To pay for higher limits, turn on billing for the key's project (see [Models](#models)).

Without a Gemini key, Olisar can't reply: every reply comes back as the **When it draws a blank** message (see [Command replies](#command-replies)). A new key comes with its own daily allowance, so models the old key had used up for the day are available again straight away.

#### Set a monthly budget

When the saved key has billing on, two settings appear under it:

| Setting | What it does | Default |
| --- | --- | --- |
| **Monthly budget** | The most Olisar spends on Gemini in a calendar month, in US dollars. 0 is no budget. | 0 |
| **At the budget** | **Keep replying on the cheapest model** answers with the cheapest models only (Gemini 2.5 Flash-Lite, then 3.1 and 3.5 Flash-Lite), with no web search and no Gemini images. **Stop until next month** stops replying: members get the **When rate-limited** reply, and background work waits for the new month. | Keep replying on the cheapest model |

From 80% of the budget, the pace line on [Usage](#usage--rate-limits) warns and the sidebar shows how much is spent, such as **Budget at 85%**. Usage also warns when the month is on pace to go over. Once the budget is spent, the sidebar shows **Budget spent**. The month starts over on the 1st, Pacific time.

The budget holds Olisar to its own estimate of what it spent (see [Usage & rate limits](#usage--rate-limits)). It doesn't limit anything else that uses the same Google Cloud project, and Google's bill is what you actually pay.

#### Turn on image generation

With billing on, Olisar makes images with Gemini, at about 3¢ an image, and Cloudflare is optional: it makes the image when Gemini can't. **Make images with Gemini**, under the Gemini key, is on by default; turn it off to use only Cloudflare. On a free key the switch shows **Needs billing**, and images need a Cloudflare API token and the ID of the account it belongs to. With neither, Olisar tells people it can't make images.

To add Cloudflare:

1. On Cloudflare's [Workers AI page](https://dash.cloudflare.com/?to=/:account/ai/workers-ai), choose **Use REST API**.
2. Choose **Create a Workers AI API Token**, then **Create API Token**, and copy the token.
3. In API keys, paste the token into **API token** under **Cloudflare Workers AI**.
4. Copy the **Account ID** shown on the same Cloudflare page into **Account ID**.
5. When **Works** appears under **Account ID**, press **Save changes**.

If you create a token yourself instead of using that button, give it the **Account > Workers AI > Read** permission. When a token can also read its own account, Olisar fills in the account ID for you; the token from the Workers AI button can't, which is why you copy the ID.

#### Key status and checks

Each field shows where its key comes from:

| Badge | Means |
| --- | --- |
| **Saved** | A key is stored in the console. The field stays empty; leave it empty to keep the key. |
| **From environment** | The key comes from an environment variable on the machine Olisar runs on, such as `GEMINI_API_KEY`. Paste a key to override it. |
| **Not set** | There's no key. |

Olisar checks each key with its service: the key you've typed, or the saved one when the field is empty. Under the field it shows **Works**, or what's wrong, such as "Google didn't accept that key." or "That token can't use this account." For a Gemini key that works, it adds whether the key is on the free tier or has billing on (see [Models](#models)). If the service can't be reached, it shows nothing rather than guess.

**Save changes** stores the key fields you've typed in and any change to the budget or Gemini images, and leaves the other keys as they are. Olisar starts using a new key within a few seconds, with no restart.

To remove a saved key, press the trash icon beside it and confirm with **Remove key**. Olisar then uses the environment variable for that key if there is one; otherwise the feature it powers stops working.

#### How keys are stored

Saved keys are stored as plain text in Olisar's database, on the machine Olisar runs on. Once saved, a key is never sent back to the browser: the field stays empty, and the console can't show or recover it. The Activity list records that keys changed, but not what they are. A key from the environment fills the field only when the console is open on that machine itself.

> [!WARNING]
> **Anyone who can read the database can read the keys**
> Keep the machine Olisar runs on, and copies of its data folder, private. [Hosting & your data](#hosting--your-data) says where the database lives.

## Knowledge & memory

### Knowledge base & glossary

The knowledge base holds web pages, crawled sites and documents that Olisar looks things up in when it answers. The glossary is a short list of facts about your server that it carries into every reply. Both live on the Knowledge tab, and each server has its own.

The **Message search index** card at the top of the same tab is covered in [Memory & search](#memory--search).

#### Add a web page or site

1. Open the Knowledge tab.
2. Under **Knowledge base**, set **Type** to **single page** or **crawl a website**.
3. Paste the address into **URL**. It has to start with `http://` or `https://`.
4. For a crawl, set **Crawl depth (0–3)**, the number of links away from the start page Olisar follows, and **Max pages**.
5. Pick how often to **Re-read** it, or leave it at **Never**.
6. Press **Add & ingest**.

The source appears under **Sources** with a **Queued** badge, then **Reading**. When the badge goes away the source has been read, and its passages become searchable over the next few minutes. Sources are read one at a time, oldest first, so a new source waits behind any crawl already in progress.

Admins can also ask Olisar in Discord to add, re-read or remove a source. Changes made that way can require the tool PIN; see [Access control](#access-control).

#### Upload a document

Documents are added from Discord, not the console. In your server, run `/olisar learn-doc` and attach a PDF, DOCX, TXT or Markdown file of up to 10 MB. Like every `/olisar` command, it needs **Manage Server**. The document then shows up under **Sources** with the rest.

Olisar reads the text layer of a PDF, so a scanned PDF with no selectable text comes back empty. From a Word file it reads the paragraphs, not text inside tables. A document has no re-read schedule: to update one, upload the new version and remove the old source.

#### Source types and limits

| | Single page | Crawled site | Uploaded document |
| --- | --- | --- | --- |
| Add it with | **single page**, or `/olisar learn-url` | **crawl a website**, or `/olisar learn-site` | `/olisar learn-doc` |
| What Olisar reads | The main text of one page | The main text of the start page and the pages it links to on the same host | All the text in the file |
| Limits | The first 10 MB of the page | Depth 0–3 (default 1), 1–100 pages (default 25), the first 10 MB of each page | PDF, DOCX, TXT or MD, up to 10 MB |
| Re-read | Any schedule | Any schedule | None. Upload it again |

Every web source follows the same rules:

- Olisar only reads public addresses. A page on your own network, such as a router or a local server, is refused, because Olisar runs inside the operator's network.
- It fetches pages signed out, so it sees what a visitor without an account sees.
- It honors `robots.txt`, so a site that turns crawlers away gives nothing.
- It only reads HTML pages, so a PDF isn't read, whether a crawl reaches it or you add it as a single page. Upload it with `/olisar learn-doc` instead.
- A crawl stays on the start address's host, so a crawl of `docs.example.com` doesn't follow links to `example.com`.
- It doesn't run a page's scripts, so a page that builds its text with JavaScript can come back empty.

#### Keep a source current

Every web source has a **Re-read** schedule, from **Never** (the default) through **Every hour**, **Daily** and **Weekly** to **Monthly**. Change it on the source's row at any time. It applies at once; there's no Save.

On a re-read, Olisar compares what it finds with the passages it already holds and indexes only the ones that changed. A page nobody edited costs nothing against your quota, and a page that gained a paragraph costs about that paragraph.

The line under each source gives its type, its passage count (shown as chunks), when it was last checked and when the next read is due. **Refresh** reads it again now and restarts the schedule; after an error the button reads **Retry**. **Remove** deletes the source and every passage read from it, and adding it back means reading the whole thing again.

> [!NOTE]
> **A failed read keeps what Olisar already learned**
> If a site is down or a page comes back empty, the source keeps the passages from its last good read and shows the error under its name. It tries again at the next scheduled read.


From Discord, `/olisar sources` lists sources with their ids and status, and `/olisar forget-source` removes one. See [Slash commands](#slash-commands).

#### How Olisar uses the knowledge base

Olisar splits each source into passages and indexes them by meaning, so a question finds the right passage even when it shares no words with it. On every reply it pulls in the four passages closest to the message, and it can search the knowledge base again when a question needs more. It answers in its own words and doesn't cite the source. Only web search results get citations.

Indexing a passage spends the same daily embedding allowance that memory search uses (see [Usage & rate limits](#usage--rate-limits)). If the allowance runs out, the rest of a source becomes searchable after the daily reset.

A focused source works better than a big one. A 25-page crawl of the pages that matter beats a 100-page crawl of a whole site, which costs more and fills the knowledge base with navigation, changelogs and archives that crowd better passages out of answers. Start a crawl at the section you want, such as `https://example.com/docs/guides`, with a low depth.

#### Glossary

The glossary holds short, server-specific facts that Olisar carries into every reply, so it knows what your abbreviations, codenames, groups and in-jokes mean. Unlike the knowledge base, it isn't searched. It's always there.

To add an entry, fill in **Subject** with the term (`MN`) and **Fact** with one standalone sentence ("MN is Movie Night, our Friday watch-party in #cinema"), then press **Add fact**. If you leave **Subject** blank, the fact's first word is used.

The glossary also grows on its own:

- Olisar mines new facts from conversation once a channel set to `memory` or `both` has gathered enough new messages. **Glossary mine threshold** on Behavior sets how much.
- A member can tell Olisar a server fact ("olisar, remember the raid team meets on Fridays"), and Olisar can add it.
- **Mine from memory** mines up to 400 messages from conversation memory that haven't been mined yet. If more are left, it says how many; press it again to continue.
- **Deep mine from index** reads the 600 most recent messages in the search index, which includes channels Olisar doesn't keep memory of.

Mining keeps one entry per subject and folds new detail into the existing one. The count under an entry ("seen 3×") is how often it has come up. **Delete** removes an entry, though Olisar may mine it again if it keeps coming up.

Three rules decide what a reply carries:

- A reply carries at most 60 entries, the most often seen first.
- A fact mined from a channel only reaches members who can open that channel. Facts you add on the Knowledge tab reach everyone.
- Olisar treats entries as claims about the server, not instructions. An entry can't grant anyone access or change Olisar's rules, however official it sounds. Since anyone who can talk to Olisar can add one, read the list now and then.

#### Activity

The **Activity** section is a log of what has been changed on this install, newest first: settings saves, source and glossary edits, re-indexes, and every destructive action with the counts it reported. Each line names the admin who did it. A change made by asking Olisar in Discord is marked **Via Discord chat**, and **Details** shows the values before and after.

The log covers every server on the install, so only the operator sees this section. The same log is under **Settings > Activity**.

#### Danger zone

**Clear memory**, at the bottom of the tab, erases everything Olisar has learned about the selected server. To confirm, you type that server's name.

| Erased | Kept |
| --- | --- |
| Conversation memory and summaries | Persona, behavior, channel modes and command replies |
| The search index | Access rules and extensions |
| Every member's impression and remembered facts | Members' opt-outs |
| The glossary | DMs, and every other server's data |
| The knowledge base | Usage stats |

Afterward, new messages are indexed as they arrive, but Olisar doesn't read back through older history until you press **Re-index all**. The Activity log keeps a line with the counts of what was erased.

> [!WARNING]
> **There's no undo**
> Check the server name in the dialog before you type it, since the server switcher is in another part of the screen. To remove only one member's data, have them run `/forget-me`.

### Memory & search

Olisar remembers conversations in the channels you choose, recalls what's relevant each time it replies, and keeps a separate index of the whole server that it searches when someone asks about something said before. Each server's memory is its own, and any member can erase their part of it with `/forget-me`.

#### What Olisar keeps

| Kind | What it is | Where it comes from |
| --- | --- | --- |
| Conversation memory | Messages, in full | Channels set to `memory` or `both`, and DMs |
| Summaries | Three to six bullet points per stretch of conversation | Written once a channel gathers enough new conversation |
| Remembered facts | Short notes about one member | Saved by Olisar during a conversation |
| Impressions | A short profile of each member | See [Members](#members) |
| Glossary | Facts about the server | See [Knowledge base & glossary](#knowledge-base--glossary) |
| Search index | Every message in every channel Olisar can read | Kept apart from memory; see below |

#### Conversation memory

Olisar stores members' messages, and its own replies, in channels set to `memory` or `both` on the Channels tab, and in DMs. Threads and forum posts follow their parent channel's mode, and each thread is its own conversation. Channels set to `respond` keep nothing, so there Olisar answers each message without the conversation before it.

Messages stay until they're deleted in Discord, erased with `/forget-me`, or wiped with **Clear memory**. Olisar stores only what it sees arrive, so anything posted while it's offline isn't in memory.

#### Summaries

Once a channel gathers enough new conversation, Olisar condenses it into a few bullet points of facts, decisions, plans and who was involved, and recalls those later. **Summary token threshold** on Behavior sets how much conversation that takes.

#### Remembered facts

Olisar saves a short note about a person when they say something worth keeping ("I'm on UTC+2", "I only play healer") or ask it to remember something. Each note is tagged as a fact, a preference or an event. An event with a date gets a follow-up: Olisar DMs the person around then.

Facts show on the Members tab, and each member can see and delete their own on the [member portal](#member-portal). Nothing is saved about a member who has asked Olisar to stop remembering them.

#### Recall

Before each reply, Olisar gathers what it needs to answer in context:

| Recalled | How much |
| --- | --- |
| The latest messages in the channel | **Context window (messages)** on Behavior, 12 by default |
| The glossary | Up to 60 entries |
| `resource` and `feed` channels | Their latest messages |
| The asker's impression and roles | In full |
| Summaries | The 3 closest in meaning to the message |
| Older messages | The 5 closest in meaning, with links |
| Facts about the asker | The 4 closest in meaning |
| Knowledge base | The 4 closest passages |

Olisar can also look through memory again partway through a reply, when someone mentions something older than what's in view. Everything recalled is treated as background, not instructions, so an old message that says "ignore your rules" is read as text.

Summaries, older messages, glossary facts and `resource` or `feed` channels come only from the channel Olisar is replying in and channels the asker can open. A reply in a public channel never draws on a private one the asker can't see.

#### The server-wide search index

The search index is a copy of every message in every channel Olisar can read, kept apart from conversation memory. It's what lets Olisar answer "what's our Twitch?" or "where was the raid schedule posted?" with a link to the message. Indexing is on for every text channel, forum post and thread from the start, including channels set to `off`. Once the operator has approved a server, Olisar reads back through each channel's history on its own.

For each message the index holds the text, the text of any embeds (so announcement posts and link previews are searchable), the names of attached files and stickers, and a short description of each posted image (see [Images](#images)). Other bots' posts are indexed, and Olisar's own aren't.

Some messages stay out:

- Channels set to **not indexed** on the Channels tab, and their threads
- Messages from members who opted out with `/forget-me stop_remembering:true`, or turned search off or paused it on the member portal
- DMs from a member who ran `/dm-indexing enabled:false`

#### How a search works

When a question is about the server's past, Olisar searches three places: the whole index by keyword, conversation memory by meaning, and the `resource` and `feed` channels. It reads the ten best hits, answers, and pastes the link to the message its answer rests on. Discord shows the link as a chip that opens the message.

For example, a member asks "olisar, when do raid sign-ups close?" and Olisar answers "friday at 8pm utc" with a link to the announcement. Before a reply is sent, Olisar removes any message link that wasn't among its search results, so every link opens the message it names.

#### Search only returns what the asker can open

Every hit is checked against the person asking before Olisar sees it. They need **View Channel** and **Read Message History** on the channel, and membership for a private thread. Someone without access to a staff channel gets nothing from it: no quote, no channel name, no link. When Olisar speaks up without being asked, nobody is asking, so it searches with what @everyone can open.

DMs are only searched inside the same DM conversation. Nobody else can reach a member's DMs this way, admins included; see [Privacy & data](#privacy--data).

> [!WARNING]
> **The check is on the asker, not the audience**
> An admin who can open a staff channel and asks about it in a public channel gets the answer, and the link, in the public channel. Ask about private channels in private ones.


#### Manage the index

The **Message search index** card at the top of the Knowledge tab shows how many messages are searchable and, while history is being read, each channel's progress: **Queued**, **Indexing…** or **Indexed**.

| Control | What it does |
| --- | --- |
| **Re-index all**, or `/olisar reindex` | Reads every indexed channel's history again and adds anything missing. Safe to run any time |
| **Clear index**, or `/olisar clear-index` | Erases this server's index and stops reading history. New messages are still indexed. To confirm in the console, type `clear index` |
| **not indexed** on a channel's row on the Channels tab | Erases that channel and its threads from the index when you save, and stops indexing it. Setting it back to **indexed** reads its history again |

Olisar reads history at about 600 messages a minute, shared by every server on the install, so a large server's history can take hours. Messages posted while Olisar was offline aren't indexed until you press **Re-index all**.

#### Edits and deletes

When someone edits a message, Olisar replaces its copy in conversation memory, the search index and the `resource` and `feed` channels. When someone deletes one, Olisar removes it from all three, so it won't quote or link a message that's gone. A summary, glossary entry or impression already written from that message stays as it is.

### Members

The Members tab shows what Olisar has learned about each member of the selected server: a short impression of them and the facts it remembers. Use it to see what Olisar has picked up, and to build or refresh a member's impression.

Any admin of the server can open the tab. Members never see it; each one can see their own facts, and their own impression if you allow it, on the [member portal](#member-portal).

#### What the tab shows

Every member of the server is listed, whether or not they've talked, except bots and members who asked Olisar to stop remembering them. The heading counts how many members Olisar knows and how many have an impression.

Each row has:

- The member's name and avatar
- Up to three of their roles, with a **+N** chip that lists the rest
- Their impression, or "No impression yet."
- The facts Olisar remembers about them, tagged **Fact**, **Pref.** or **Event**

Members with an impression come first, then members with only remembered facts, then everyone else. The filter box matches names, roles and words in an impression.

#### Impressions

An impression is three to six sentences on how a member comes across: their interests and expertise, how they talk, and stable facts they've stated, such as their timezone. If a `resource` channel like `#roles-list` explains a role they hold, the impression says what the role means for them. Olisar is told not to speculate or infer sensitive traits. It reads the impression of whoever it's replying to, so it can talk to each person in a way that fits them.

Olisar builds an impression on its own once a member has sent **Persona rebuild (messages)** new messages, 15 by default, set on the Behavior tab. It needs at least 8 of their messages in conversation memory. Each rebuild starts from the existing impression, keeps what's still true and adds what's new.

Only messages in channels set to `memory` or `both` count toward that, so a member who posts mostly elsewhere stays at "No impression yet." until you build one.

#### Build or rebuild an impression

1. Find the member on the Members tab.
2. Press **Create impression**, or **Rebuild impression** if they already have one.
3. For a rebuild, confirm with **Rebuild**.

Olisar reads up to the member's last 60 messages, from conversation memory first and then from the [search index](#memory--search), so it works for members who mostly post in channels it doesn't remember. It needs at least 3 messages; with fewer, the row says how many it found. Each build uses model quota, and if the models are busy the row says so and you can try again a moment later.

#### Remembered facts

Olisar saves facts about a member during conversations, as described in [Memory & search](#memory--search). A fact saved in a member's DMs with Olisar is filed under the [main server](#servers), so it shows on that server's Members tab.

#### What you can change

You can't edit or delete an impression or a fact on this tab. Your controls are rebuilding an impression and the server-wide wipe:

| Who | Where | What it erases |
| --- | --- | --- |
| The member | `/forget-me` in Discord | Their messages, facts, impression and search index entries |
| The member | The member portal, if you've opened it | A single fact, or everything |
| An admin | **Clear memory** on the Knowledge tab | Everything Olisar learned about the server, every member included |

Impressions are written by the model and can be wrong or unflattering. Read a few here before you turn on **Show each member their impression** for the portal. What members can see, export and erase is covered in [Privacy & data](#privacy--data).

### Images

Olisar looks at images people post to it, describes the images members upload so search can find them later, and draws new images when someone asks. Seeing and describing images run on Gemini. Drawing them runs on Gemini too when the key has billing on, and otherwise needs a Cloudflare key; without either, Olisar tells members it can't make images.

#### Seeing posted images

When a message that addresses Olisar has images in it, Olisar sees them along with the text and can talk about what's in them: read a screenshot's error, identify a ship, rate a build.

- It looks at up to 3 images per message, each up to 5 MB.
- It sees a GIF as a still of its first frame and knows it's only one frame. GIFs from Discord's GIF picker and Tenor or Giphy links work too.
- It only sees images in the message that addresses it. If a member replies to someone else's picture with "olisar, what's this?", Olisar gets the file name and at most a short description, not the picture. For a proper look, the image has to be in the same message.
- `/ask` takes text only, so it can't show Olisar an image.

#### Descriptions for search

Every image a member uploads to an indexed channel gets a one- or two-sentence description, including any text, usernames, links or logos visible in it. The description is added to the stored message, so a later search for "the screenshot with the 30k error" finds it. Channels set to `off` count too, since indexing doesn't depend on a channel's mode, and each image is described once.

Older images are described while Olisar reads back through history, at about three a minute. When the Gemini models that write descriptions are busy, Olisar skips the image, and only its file name is searchable.

Descriptions use your Gemini quota and show as **Image** in the by-feature breakdown on the Usage tab. To stop them in a channel, set it to **not indexed** on the Channels tab, which also takes the channel out of search. See [Memory & search](#memory--search) for the index.

#### Generate images

Members ask in plain language: "olisar, draw a neon space whale over the city". Olisar writes a detailed prompt from the request, posts the image to the channel and adds a short caption. Anyone who can talk to Olisar can ask; there's no separate switch for image generation.

Where the image is made depends on the Gemini key:

| Key | Images come from |
| --- | --- |
| Billing on | Gemini's Nano Banana 2 Lite (`gemini-3.1-flash-lite-image`), at about 3¢ an image on the key's bill. If Gemini fails and Cloudflare is set up, Cloudflare makes it instead |
| Free | [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/) with the FLUX.1 [schnell] model, because Gemini's image models aren't on the free tier. It needs the Cloudflare account ID and API token |

**Make images with Gemini** on the API keys tab turns Gemini images off, leaving Cloudflare. Gemini images also stop once the month's budget is spent. See [API keys](#api-keys) for both, and for getting the Cloudflare keys.

#### Limits

- Olisar makes at most 2 images per reply. Ask again in another message for more.
- Cloudflare's free allocation is 10,000 Neurons a day across your account, resetting at 00:00 UTC. On Cloudflare's Workers Free plan, generation stops when it's used up. On Workers Paid, usage past it is billed to your Cloudflare account.
- Olisar only makes new images. It can't edit a posted image.
- Generation isn't available in the console's **Test chat**.

#### When Olisar can't make an image

Olisar tells the member it can't make an image right now when nothing can make it: Gemini can't (a free key, **Make images with Gemini** off, the budget spent, or a failed request) and Cloudflare isn't set up, its key is wrong, or the day's allocation is used up. It gives the same answer for all of these, so if images stop working, check the API keys tab first. The operator can see the exact error from Gemini or Cloudflare in the bot's logs under **Settings > Logs**.

## Extend

### Extensions

Extensions are optional packages that add features to Olisar: things it can look up or do while it talks, slash commands, and actions it takes on its own, such as greeting new members. You turn each one on or off per server on the Extensions tab.

Any admin can turn extensions on and off and change their settings in a server. Adding, editing and deleting extensions is up to the operator, the person who runs Olisar (see [Hosting & your data](#hosting--your-data)).

#### Turn an extension on

1. Open the Extensions tab.
2. Select the extension in the list on the left.
3. Turn on the switch at the top right of its panel.
4. Press **Save changes**.

It takes effect on Olisar's next message, with no restart. The switch applies only to the server you're managing, and every server keeps its own set (see [Servers](#servers)). Extensions start off in every server unless the operator wrote one to start on.

If the extension has options, a **Settings** section appears under its panel. Fill it in and press **Save settings**. Settings are per server too.

Some extensions add sources to the knowledge base or entries to the glossary when you turn them on in a server. They do it again each time the extension goes from off to on, and skip anything that's already there.

#### What the panel shows

| Part | What it tells you |
| --- | --- |
| Badge next to the name | Where it came from: **Built-in** (ships with Olisar), **Custom** (written in this console), **Imported** (from an `.olx` file) or **Marketplace**. **Edited** means the operator changed a built-in's code. |
| **What it adds** | Tools Olisar can use in conversation, shown as `name()`, slash commands, shown as `/name`, and **Shapes replies** when it adds an instruction to how Olisar answers |
| **Capabilities it uses** | What it's allowed to do, such as make web requests or post in your channels. See [Security & trust](#security--trust). |
| **Requested but not granted** | Capabilities an imported or marketplace extension asked for that the operator didn't allow. Those parts of it won't work. |
| **Settings** | Options the extension declares, set per server |
| **Keys** | Keys the extension can use, shared by the whole install. Only the operator sees this section. |

The search box and the **All**, **Enabled** and **Custom** filters narrow the list. **Custom** shows every extension that didn't ship with Olisar.

#### Built-in extensions

Olisar ships with two extensions. Both start off.

| Extension | What it does |
| --- | --- |
| **Welcome messages** | Greets each new member in a channel you pick, in Olisar's voice |
| **Star Citizen** | Looks up live Star Citizen trade, ship and location data in conversation, adds a `/citizen` profile command, and adds RSI's Comm-Link to the knowledge base |

#### Welcome messages

Welcome messages posts a short greeting for each person who joins, written fresh each time in Olisar's persona.

1. Select **Welcome messages**, turn it on and press **Save changes**.
2. Under **Settings**, pick a **Channel**.
3. Write a **Prompt**, an instruction layered on top of the persona, such as "warmly welcome {user} and ask what brought them here".
4. Press **Save settings**.

In the prompt, `{user}` becomes the new member's display name in the server and `{username}` their Discord username. Each greeting mentions the new member and is written as if Olisar had been asked in that channel, so it can pick up the channel's topic and recent conversation.

Nothing posts until both the channel and the prompt are set. Bots that join aren't greeted. Olisar posts at most five welcomes a minute in a server, so during a burst of joins, anyone past the fifth in a minute gets no greeting. Each greeting uses your Gemini quota (see [Usage & rate limits](#usage--rate-limits)).

#### Star Citizen

Star Citizen is for Star Citizen communities. With it on, members can ask Olisar things like "is the exec hangar open?" or "best trade route for Laranite?", and it looks up live figures:

| Topic | What Olisar can look up |
| --- | --- |
| Trading | A commodity's prices, the best terminals to buy and sell it, profitable trade routes, the top earners, and what terminal stock labels mean |
| Ships | Specs from RSI's ship matrix, cargo and crew from UEX, the pledge-store price in USD, and the cheapest place to buy one in-game |
| Universe | Star systems, planets, moons, Lagrange points, points of interest, jump points, and items such as coolers and weapons |
| Live status | The Pyro Executive Hangar timer and the aUEC purchasing-power index |

Most of the data comes from [UEX](https://uexcorp.uk/). Ship specs come from RSI's [ship matrix](https://robertsspaceindustries.com/ship-matrix) and the hangar timer from the [community tracker](https://exec.xyxyll.com/). If a site doesn't answer, Olisar says so. Names are matched loosely, so a small typo still finds the right commodity or place.

The extension also adds `/citizen <username>`, which anyone can run. It replies with a card from the player's RSI profile: citizen record, enlisted date, location, fluency, and main organization with rank.

Turning it on in a server adds the [RSI Comm-Link](https://robertsspaceindustries.com/en/comm-link) page to that server's knowledge base (see [Knowledge base & glossary](#knowledge-base--glossary)).

The UEX lookups work without a key. The operator can add a free UEX API token under **Keys** on the extension's panel to raise UEX's rate limits. It's one token for the whole install.

#### Slash commands from extensions

An extension's slash commands appear in every server the bot is in, whether or not the extension is on there. In a server where it's off, running one only replies that the extension is off. Some extension commands are limited to members with **Manage Server**, and you can change who sees any command under **Server Settings > Integrations** in Discord.

#### Turn an extension off in a hurry

Run `/killswitch` in Discord to turn an extension off in that server at once, or turn off every extension there. See [Slash commands](#slash-commands). Turn it back on from the Extensions tab.

#### Add more extensions

The operator can write one in the console (see [Write an extension](#write-an-extension)), import an `.olx` file someone sent (see [Share extensions as files](#share-extensions-as-files)), or install one from [the marketplace](#the-marketplace). Before you install someone else's, read [Security & trust](#security--trust).

### Write an extension

You can write your own extension in TypeScript in the console's code editor. It runs in Olisar's sandbox and can give Olisar new tools to use in conversation, add slash commands and buttons, add sources to the knowledge base, and declare a settings form for admins.

Only the operator can write, edit or delete extension code. Admins turn your extension on and configure it per server like any other (see [Extensions](#extensions)).

#### Open the editor

On the Extensions tab, press **New extension** to start from a template, or select an extension and press its **Edit code** button (the pencil) to open its code. The editor loads the SDK's type definitions, so you get autocomplete for `defineExtension` and `host`, signatures on hover, and type errors underlined as you type. The **SDK reference** button above the editor opens the [SDK reference](#sdk-reference).

#### The shape of an extension

An extension is one call to `defineExtension({ ... })` at the top level of the file. `defineExtension` and `host` are globals, so you don't import anything, and an `import` statement makes the code fail to load.

This extension gives Olisar a dice-rolling tool. It lists no permissions because it doesn't reach outside the sandbox:

```ts
defineExtension({
  id: "dice",
  name: "Dice",
  version: "1.0.0",
  category: "Fun",
  description: "Rolls dice in standard notation, like 2d6+3.",
  permissions: [],
  tools: [
    {
      name: "roll_dice",
      description: "Roll dice in standard notation (1d20, 2d6+3). Use when someone asks you to roll.",
      parameters: {
        type: "object",
        properties: { notation: { type: "string", description: "dice notation, e.g. 2d6+3" } },
        required: ["notation"],
      },
      handler: (args) => {
        const m = /^(\d*)d(\d+)([+-]\d+)?$/.exec(String(args.notation).replace(/\s/g, ""));
        if (!m) return "I can roll dice like 1d20 or 2d6+3.";
        const count = Number(m[1] || 1), sides = Number(m[2]), mod = Number(m[3] || 0);
        if (count < 1 || count > 100 || sides < 2) return "I can roll 1 to 100 dice with 2 or more sides.";
        const rolls = Array.from({ length: count }, () => 1 + Math.floor(Math.random() * sides));
        const total = rolls.reduce((a, b) => a + b, 0) + mod;
        return args.notation + ": [" + rolls.join(", ") + "] = " + total;
      },
    },
  ],
});
```

Once it's saved and turned on in a server, a member can ask "roll 2d6+3 for me". Olisar's model calls `roll_dice`, gets back a string like `2d6+3: [5, 6] = 14`, and answers in its own words. The tool's `description` is how the model decides when to call it, so say plainly what it's for.

#### Validate and save

Press **Validate** to check the code without saving. It shows the extension's id and how many tools and commands it declares, or the first error. Press **Create extension** for a new one or **Save changes** after an edit. <kbd>Cmd</kbd>+<kbd>S</kbd> or <kbd>Ctrl</kbd>+<kbd>S</kbd> saves too.

On save, Olisar converts your TypeScript to JavaScript on its own side by stripping the types. It doesn't check types, so a type error the editor underlines won't stop a save, but a syntax error will. It then runs the code once in the sandbox to read what `defineExtension` declares, and refuses the save when:

- the `id` isn't 2 to 64 lowercase letters, digits and underscores, starting with a letter
- the `id` differs from the one you saved before (an id can't change, so make a new extension instead)
- another extension already uses the `id`
- a slash command has the name of one of Olisar's own commands (`ask`, `catchup`, `dm-indexing`, `forget-me`, `killswitch`, `olisar`, `ping` and `privacy`) or of another extension's command
- a tool has the name of one of Olisar's own tools, such as `remember` or `web_search` (the error names the clash)
- a field has the wrong type, such as a `name` that isn't a string

After a save, the extension's tools are available on Olisar's next reply in servers where it's on, and its slash commands register with Discord within a few seconds. A new extension starts off in every server unless it sets `defaultEnabled: true`, so turn it on where you want it.

> [!TIP]
> **Prefix your tool names**
> Two extensions that are on in the same server shouldn't share a tool name. Starting each name with your extension's id, the way Star Citizen uses `uex_` and `sc_`, keeps them apart.


#### How your code runs

Each time a tool, command or button runs, Olisar loads your whole file into a fresh sandbox and calls that one handler. A value you keep in a top-level variable is gone by the next call, so store anything that should last with `host.kv`.

The sandbox is plain JavaScript with no browser or Node APIs: there's no `console`, `fetch` or `setTimeout`. Use `host.log` to write a line to the bot's log, which the operator reads under **Settings > Logs**, and `host.fetch` to call the web. Every run has a CPU, wall-clock and memory budget. The [SDK reference](#sdk-reference) lists the budgets and everything else the runtime lacks.

When a tool throws or runs past its budget, Olisar's model is told the tool failed and says it couldn't do it. When a slash command fails, the member gets a private `that command hit an error.` To control what members hear, catch errors in a tool and return a short string such as "Couldn't reach the status service."

#### Permissions

To reach anything outside the sandbox through `host`, list the permission in `permissions`, such as `"fetch"` for web requests or `"kv"` for storage. Code you save yourself gets every permission it lists. A `host` call without its permission throws an error naming the permission it needs. The [SDK reference](#sdk-reference) lists every permission, and [Security & trust](#security--trust) explains the stricter rules for extensions installed from a file or the marketplace.

#### Edit a built-in

Welcome messages and Star Citizen are written with the same SDK, so **Edit code** on either opens a working example. Star Citizen is the larger one, with web requests, a slash command and a knowledge-base source.

You can change a built-in and save it too. It gets an **Edited** badge. Built-ins can't be deleted, but you can turn them off.

> [!WARNING]
> **A newer built-in replaces your edits**
> Your edits survive app updates until a release ships that built-in with a higher `version` than your code has. Then the shipped code replaces yours. To keep a change for good, copy the code into a **New extension** with a different `id`.


#### Delete an extension

Open the extension in the editor, press **Delete**, and type `delete <id>` to confirm. Deleting removes its code, its stored data and its settings in every server, and it can't be undone.

#### Next steps

To add slash commands, forms and buttons, see [Commands & interactions](#commands--interactions). To give your extension to someone else, see [Share extensions as files](#share-extensions-as-files) or publish it to [the marketplace](#the-marketplace).

### SDK reference

The extension SDK is two globals: `defineExtension`, which declares what your extension adds, and `host`, which is how your code reaches anything outside the sandbox. This page covers both. Slash commands, forms, buttons, embeds and files have their own page, [Commands & interactions](#commands--interactions).

The editor loads these same definitions as TypeScript types, so autocomplete and hover show the signatures below.

#### host at a glance

| Method | Permission | Works in |
| --- | --- | --- |
| `host.fetch(url, init?)` | `fetch` | Any handler |
| `host.kv.get`, `set`, `delete` | `kv` | Any handler |
| `host.kb.addSource(seed)` | `kb.write` | Any handler |
| `host.glossary.add(fact)` | `glossary.write` | Any handler |
| `host.settings.get(key?)` | None | Any handler |
| `host.secret(ref)` | `secret:<ref>` | Any handler, built-ins and your own extensions only |
| `host.generate(opts)` | `model.generate` | Any handler |
| `host.discord.send(channel, payload)` | `discord.send` | Tools and event handlers |
| `host.embed(spec)` | None | Any handler |
| `host.files.read`, `ingest` | None | Slash commands |
| `host.files.from(spec)` | None | Any handler |
| `host.log(message)` | None | Any handler |

`host.embed` and `host.files` are covered in [Commands & interactions](#commands--interactions). Every method except `host.embed` returns a promise, so `await` it.

#### defineExtension

```ts
declare function defineExtension(spec: ExtensionSpec): void;
```

Call it exactly once, at the top level of the file.

| Field | Type | Default | What it does |
| --- | --- | --- | --- |
| `id` | `string` | Required | The extension's key: 2 to 64 lowercase letters, digits and underscores, starting with a letter. It can't change after the first save. |
| `name` | `string` | The `id` | Display name in the console |
| `version` | `string` | `"1.0.0"` | Shown on the panel and carried in shared and published copies. Each marketplace release needs a new one. |
| `description` | `string` | `""` | One-line summary on the panel and in the marketplace |
| `category` | `string` | `"General"` | Groups the extension on the Extensions tab and in the marketplace |
| `permissions` | `Permission[]` | Required | The capabilities your code uses. See Permissions below. |
| `systemNote` | `string` | `""` | Text added to Olisar's system prompt in servers where the extension is on. Use it to say when to call your tools. |
| `defaultEnabled` | `boolean` | `false` | Starts the extension on in every server. Ignored for imported and marketplace extensions. |
| `tools` | `ToolDef[]` | None | Functions Olisar's model can call in conversation |
| `commands` | `CommandDef[]` | None | Slash commands. See [Commands & interactions](#commands--interactions). |
| `components` | `Record<string, ComponentHandler>` | None | Handlers for persistent buttons and menus. See [Commands & interactions](#commands--interactions). |
| `seeds` | `{ kbSources?, glossary? }` | None | Knowledge sources and glossary entries to add when an admin turns the extension on |
| `settingsSchema` | `{ fields: SettingsField[] }` | None | A settings form admins fill in per server |
| `events` | `{ memberJoin?: EventHandler }` | None | Handlers for Discord events. Built-ins and your own extensions only. |
| `onEnable` | `(ctx: { guildId: string }) => void` | None | Runs when an admin turns the extension on in a server. May be `async`. |

#### Permissions

Built-ins and extensions written in the console get every permission they list. Imported and marketplace extensions get only the ones the operator ticks when installing, and some capabilities stay closed to them whatever was ticked.

| Permission | Unlocks | Imported and marketplace extensions |
| --- | --- | --- |
| `fetch` | `host.fetch` | If granted |
| `kv` | `host.kv` | If granted |
| `kb.write` | `host.kb.addSource` | If granted |
| `glossary.write` | `host.glossary.add` | If granted |
| `discord.reply` | `reply` and `followUp` in a command, `reply` in a button handler | If granted |
| `discord.modal` | `modal` in a command | If granted |
| `discord.components` | `awaitComponent` in a command, `update` and `deferUpdate` in a button handler | If granted |
| `discord.send` | `host.discord.send` | If granted, and their posts can't mention anyone |
| `model.generate` | `host.generate` | If granted, but never with `channelId` |
| `secret:<ref>` | `host.secret("<ref>")` | Never |

A `host` call without its permission throws `this extension isn't allowed to use '<permission>'`. Event handlers aren't a permission: they run for built-ins and your own extensions and never for imported or marketplace ones.

#### Tools

```ts
interface ToolDef {
  name: string;
  description: string;
  parameters: JSONSchema;
  handler(args: Record<string, any>, ctx: ToolCtx): Promise<string> | string;
}

interface ToolCtx {
  guildId: string;
  channelId: string;
  userId: string;      // the member Olisar is replying to
  displayName: string;
}
```

A tool is a function Olisar's model can call while it writes a reply in a server where your extension is on. The model reads `description` to decide when to call it and fills `args` to match `parameters`.

`parameters` takes a subset of JSON Schema: `type` (`"object"`, `"string"`, `"number"`, `"integer"`, `"boolean"` or `"array"`), `description`, `properties`, `required`, `items` and `enum`. The top level should be an `object`, even an empty one.

Return a short string. Olisar puts it in its own words, so plain facts work better than formatted prose. If the handler returns nothing, the model hears that the tool returned nothing, and any other non-string value is passed as JSON. If it throws or runs past its budget, the model hears that the tool failed. Catch your own errors and return a sentence instead, so the member hears something useful.

This tool reads a server address from the extension's settings and checks it with a public API:

```ts
defineExtension({
  id: "mc_status",
  name: "Minecraft status",
  version: "1.0.0",
  category: "Gaming",
  description: "Tells members whether the community's Minecraft server is up.",
  permissions: ["fetch"],
  settingsSchema: {
    fields: [{ key: "address", type: "text", label: "Server address", desc: "For example play.example.com" }],
  },
  systemNote: "When someone asks whether the Minecraft server is up or who's on, use mc_server_status.",
  tools: [
    {
      name: "mc_server_status",
      description: "Check whether this community's Minecraft server is online and how many players are on.",
      parameters: { type: "object", properties: {} },
      handler: async () => {
        const address = await host.settings.get("address");
        if (!address) return "No server address is set. An admin can add one in the extension's settings.";
        let r;
        try {
          r = await host.fetch("https://api.mcsrvstat.us/3/" + encodeURIComponent(address));
        } catch (e) {
          return "Couldn't reach the status service.";
        }
        if (!r.ok) return "The status service answered " + r.status + ".";
        const body = await r.json();
        if (!body.online) return address + " is offline.";
        return address + " is online with " + body.players.online + " of " + body.players.max + " players.";
      },
    },
  ],
});
```

A tool can't use a name that belongs to one of Olisar's own tools, such as `remember` or `web_search`; saving or installing it is refused.

#### Seeds and onEnable

`seeds` adds knowledge sources and glossary entries to a server when an admin turns the extension on there. `onEnable` runs right after, for setup the seeds can't express:

```ts
defineExtension({
  id: "guild_basics",
  name: "Guild basics",
  version: "1.0.0",
  permissions: ["kv"],
  seeds: {
    kbSources: [{ type: "website", uri: "https://wiki.example.com", title: "Guild wiki" }],
    glossary: [{ subject: "Raid night", fact: "Raids start Thursdays at 20:00 UTC in #raids." }],
  },
  async onEnable(ctx) {
    if (!(await host.kv.get("config"))) {
      await host.kv.set("config", { raidDay: "Thursday", enabledIn: ctx.guildId });
    }
  },
});
```

| Seed | Shape | Notes |
| --- | --- | --- |
| `kbSources` | `{ type, uri, title }` | `type` is `"url"` for one page (the default) or `"website"` to crawl a site. A `uri` already in that server's knowledge base is skipped. See [Knowledge base & glossary](#knowledge-base--glossary). |
| `glossary` | `{ subject, fact }` | The glossary keeps one entry per subject. Only the first 20 entries are applied. |

Seeds don't need `kb.write` or `glossary.write`. Both seeds and `onEnable` run each time the extension goes from off to on in a server, so make `onEnable` safe to repeat. Neither runs for an extension that's on by default until someone turns it off and on again. `onEnable` can't use Discord actions, and if it throws, the extension still turns on and the error goes to the bot's log.

#### Settings form

`settingsSchema` declares a form that appears under **Settings** on the extension's panel. Admins fill it in per server, and your code reads it with `host.settings.get`.

```ts
interface SettingsField {
  key: string;
  type: "text" | "textarea" | "channel" | "number" | "toggle";
  label: string;
  desc?: string;  // help text under the field
}
```

| `type` | Admins see | Your code gets |
| --- | --- | --- |
| `text` | A one-line text box | A string |
| `textarea` | A multi-line text box | A string |
| `channel` | A channel picker | The channel's id, as a string |
| `number` | A number box | A number |
| `toggle` | A switch | A boolean |

#### Events

```ts
interface EventContext {
  event: string;          // "memberJoin"
  guildId: string;
  member: EventMember | null;
}

interface EventMember {
  id: string;
  displayName: string;    // server nickname or display name
  username: string;
  mention: string;        // "<@id>", to ping them
  bot: boolean;
}
```

`events.memberJoin` runs when a person joins a server where the extension is on. Bots joining don't trigger it. It's the only event. There's no interaction to reply to, so post with `host.discord.send`, and because no member set it off, a `channelId` passed to `host.generate` only has to be in the same server.

Event handlers run only for built-ins and extensions written in the console. An imported or marketplace extension can declare them, but they never run. The built-in Welcome messages extension is a fuller example of this one:

```ts
defineExtension({
  id: "greeter",
  name: "Greeter",
  version: "1.0.0",
  category: "Automation",
  description: "Welcomes new members in a channel you pick.",
  permissions: ["model.generate", "discord.send"],
  settingsSchema: {
    fields: [{ key: "channel_id", type: "channel", label: "Welcome channel" }],
  },
  events: {
    async memberJoin(ctx) {
      const channelId = await host.settings.get("channel_id");
      if (!channelId || !ctx.member) return;
      const text = await host.generate({
        task: "Welcome " + ctx.member.displayName + " to the server in one or two warm sentences.",
        channelId: channelId,
        maxTokens: 200,
      });
      await host.discord.send(channelId, ctx.member.mention + " " + text);
    },
  },
});
```

#### host.fetch

```ts
host.fetch(url: string, init?: FetchInit): Promise<FetchResponse>
```

Calls a web API. Needs `fetch`.

| `init` option | Type | What it does |
| --- | --- | --- |
| `method` | `string` | `GET` (the default), `POST`, `PUT`, `PATCH`, `DELETE` or `HEAD` |
| `headers` | `Record<string, string>` | Request headers |
| `body` | `string` | The request body, such as JSON you've passed through `JSON.stringify` |
| `bodyBlobId` | `string` | Send a host-held file as the raw body instead. See [Commands & interactions](#commands--interactions). |
| `responseBlob` | `boolean` | Keep the response body on the host as a file and return its `blobId` instead of text |

| Response field | Type | What it holds |
| --- | --- | --- |
| `status` | `number` | The HTTP status |
| `ok` | `boolean` | `true` for a 2xx status |
| `headers` | `Record<string, string>` | Response headers, with lowercase names |
| `text()` | `Promise<string>` | The body as text. Empty when `responseBlob` is set. |
| `json()` | `Promise<any>` | The body parsed as JSON |
| `blobId`, `size`, `contentType` | | The stored file, when `responseBlob` is set |

The rules:

- Only `http` and `https` URLs work. The host name has to resolve to a public address: loopback, private-network and link-local addresses are refused, and so is a redirect to one.
- A call follows up to 5 redirects.
- An error status such as 404 doesn't throw, so check `ok` or `status`. A refused URL, a timeout or a network failure does throw.
- A handler run can make up to 30 calls.
- A call times out after 15 seconds, or 90 seconds when it sends or receives a blob, and the time also counts against the run's wall-clock budget.
- A response body can be up to 20 MB as text, or 25 MB with `responseBlob`.

#### host.kv

```ts
host.kv.get(key: string): Promise<any>
host.kv.set(key: string, value: any): Promise<void>
host.kv.delete(key: string): Promise<void>
```

Your extension's own storage, separate for each server. Needs `kv`. Values are stored as JSON, and `get` returns `null` for a key that isn't set.

```ts
const counts = (await host.kv.get("counts")) || {};
counts[ctx.userId] = (counts[ctx.userId] || 0) + 1;
await host.kv.set("counts", counts);
```

| Limit | Value |
| --- | --- |
| Key length | 128 characters |
| One value, as JSON | 1 MB |
| Keys per extension per server | 10,000 |
| Total per extension per server | 32 MB |

A `set` that would pass a limit throws. If a slash command, button or event handler throws or runs out of time, the writes from that run are discarded. Deleting the extension deletes its storage in every server.

#### host.kb.addSource and host.glossary.add

```ts
host.kb.addSource(seed: { type?: "url" | "website"; uri: string; title?: string }): Promise<boolean>
host.glossary.add(fact: { subject: string; fact: string }): Promise<number>
```

`addSource` adds a source to the current server's knowledge base and needs `kb.write`. It resolves to `true` when the source is queued for reading and `false` when that `uri` is already there. `glossary.add` adds a glossary entry and needs `glossary.write`. It resolves to `1` for a new subject and `0` when the subject exists; in that case the stored fact is replaced only when the new one adds detail.

#### host.settings.get

```ts
host.settings.get(key?: string): Promise<any>
```

Reads what admins entered in your settings form for the current server. `get()` returns the whole object and `get(key)` one value, or `null` if nobody has filled it in. It needs no permission and can't write.

#### host.secret

```ts
host.secret(ref: string): Promise<string | null>
```

Reads one of the install's API keys by name. Needs `secret:<ref>` in `permissions`, such as `"secret:uex_api_key"`.

| `ref` | The key |
| --- | --- |
| `uex_api_key` | The UEX API token, set under **Keys** on the panel of any extension that lists this permission |
| `gemini_api_key` | The Gemini API key |
| `cloudflare_account_id` | The Cloudflare account ID |
| `cloudflare_api_token` | The Cloudflare API token |

It resolves to `null` when the key isn't set, and throws for any other `ref`. Secrets are for built-ins and your own extensions: for an imported or marketplace extension, the call always throws. The Gemini and Cloudflare keys are the ones on the [API keys](#api-keys) page.

#### host.generate

```ts
host.generate(opts: GenerateOpts): Promise<string>
```

Writes text with Olisar's model in the server's persona and resolves to it. Needs `model.generate`, and each call uses the operator's Gemini quota (see [Usage & rate limits](#usage--rate-limits)).

| Option | Type | What it does |
| --- | --- | --- |
| `task` | `string` | What to write. Required. |
| `maxTokens` | `number` | The longest output. Defaults to 600; the host caps it at 1200. |
| `systemNote` | `string` | An extra instruction added to the system prompt for this call |
| `channelId` | `string` | Write as if Olisar had been called in this channel: a channel id or `<#id>` mention |

With `channelId`, the model sees what a reply in that channel would: the channel's name and topic, its recent conversation, and the server's glossary. That option has extra rules, and the call throws when one isn't met:

- It works only for built-ins and your own extensions.
- The channel has to be in the same server, and one the bot already knows. A channel created a moment ago may not be known yet, so catch the error and call again without `channelId`.
- When a member set off the run with a command, a button or a message Olisar is replying to, the channel has to be one that member can open.
- It can't be used while replying to a DM.

#### host.discord.send

```ts
host.discord.send(
  channel: string,
  payload: string | { content?: string; embed?: any; components?: Component[]; files?: FileOut[] },
): Promise<string>
```

Posts a message to a channel without an interaction to answer. Needs `discord.send`. It works from tools and event handlers. In a slash command or button handler it throws, because those reply with the interaction instead (see [Commands & interactions](#commands--interactions)).

| Called from | `channel` can be | The promise resolves to |
| --- | --- | --- |
| A tool | A channel id, a `<#id>` mention or a channel name, in the same server. The current channel's id posts where the conversation is. | A short status such as `Posted in #general.`, or why it couldn't post |
| An event handler | A channel id | `null` |

From a tool, `content` is cut at 2,000 characters; from an event handler, longer text is split into several messages. `components` can hold persistent buttons and menus, which keep working. An extension can post at most 5 messages a minute in a server; past that, nothing is posted and the promise resolves to a message saying the post was rate-limited.

Mentions depend on where the extension came from. A built-in's or your own extension's post can ping the members it mentions. An imported or marketplace extension's post pings nobody. No extension can ping `@everyone`, `@here` or a role.

#### host.log

```ts
host.log(message: string): Promise<void>
```

Writes a line to the bot's log as `ext[<id>]: <message>`. It needs no permission. The operator reads the log under **Settings > Logs**.

#### Runtime and limits

Each time a handler runs, Olisar loads your whole file into a fresh JavaScript context and calls that one handler. Top-level variables don't carry over between calls, and your top-level code runs at the start of every call.

The context runs modern JavaScript (ES2020, with `async` and `await`) and nothing else. There's no `console`, `fetch`, `setTimeout`, `require`, `atob`, `btoa`, `TextEncoder` or `Intl`, and `import` fails. Use `host` for everything outside the sandbox.

| Handler | CPU time | Wall clock | Memory |
| --- | --- | --- | --- |
| Tool | 5 seconds | 20 seconds | 64 MB |
| Slash command | 10 seconds | 15 minutes | 128 MB |
| Persistent button or menu | 10 seconds | 30 seconds | 64 MB |
| Event handler | 10 seconds | 60 seconds | 64 MB |
| `onEnable` | 10 seconds | 15 minutes | 64 MB |

CPU time counts only the time your JavaScript spends running. Wall clock counts everything, including time spent waiting on `host` calls. Reading your extension's declaration on save, import or install gets 5 seconds of CPU and 20 seconds overall. A run that passes a limit is stopped and treated as a failure.

### Commands & interactions

An extension can add slash commands, and a command can run a short exchange with the member: reply, ask with a form, offer buttons, post an embed or send a file. Buttons can also outlive the command and keep working for everyone, which is how polls and RSVPs work.

Each kind of response needs a permission: `discord.reply` to reply, `discord.modal` for forms, and `discord.components` to wait on or update buttons. The fields of `defineExtension` and the rest of `host` are in the [SDK reference](#sdk-reference).

#### Add a slash command

Declare commands in `commands`. This pair, modeled on the marketplace's Server Tags extension, saves answers and shows them on request:

```ts
defineExtension({
  id: "faq",
  name: "FAQ",
  version: "1.0.0",
  category: "Community",
  description: "Saved answers members can pull up with /faq.",
  permissions: ["kv", "discord.reply"],
  commands: [
    {
      name: "faq",
      description: "Show a saved answer.",
      options: [{ name: "topic", description: "the topic to show", type: "string", required: true }],
      handler: async (i) => {
        const faq = (await host.kv.get("faq")) || {};
        const entry = faq[String(i.options.topic).toLowerCase()];
        if (!entry) {
          await i.reply({ content: "Nothing saved for " + i.options.topic + ".", ephemeral: true });
          return;
        }
        await i.reply(entry.text);
      },
    },
    {
      name: "faqset",
      description: "Save or update an answer.",
      defaultMemberPermissions: "manage_guild",
      options: [
        { name: "topic", description: "one or two words", type: "string", required: true },
        { name: "text", description: "the answer", type: "string", required: true },
      ],
      handler: async (i) => {
        const faq = (await host.kv.get("faq")) || {};
        faq[String(i.options.topic).toLowerCase()] = { text: i.options.text, by: i.userId };
        await host.kv.set("faq", faq);
        await i.reply({ content: "Saved " + i.options.topic + ".", ephemeral: true });
      },
    },
  ],
});
```

| Field | Type | What it does |
| --- | --- | --- |
| `name` | `string` | The command, without the slash: 1 to 32 lowercase letters, digits, hyphens and underscores |
| `description` | `string` | Shown in Discord's command picker. Cut to 100 characters. |
| `options` | `OptionDef[]` | The inputs, each `{ name, description, type, required }`. See Options below. |
| `defaultMemberPermissions` | `"manage_guild"` or `null` | `"manage_guild"` shows the command only to members with **Manage Server**. `null`, the default, shows it to everyone. |
| `guildOnly` | `boolean` | Defaults to `true`. Extension commands are registered in each server and never appear in DMs, whatever this says. |
| `handler(i)` | function | Runs when someone uses the command. `i` is the interaction. |

`defaultMemberPermissions` also takes other Discord permission names in lowercase, such as `"manage_messages"`, though the editor's types only list `"manage_guild"`. A name Discord doesn't know limits the command to members with **Manage Server**. Server admins can change who sees any command under **Server Settings > Integrations**.

> [!NOTE]
> **Some names save but never register**
> Option names become parameters on Olisar's side, so they have to be lowercase letters, digits and underscores, can't be a reserved word such as `from` or `class`, and can't be `interaction`. Required options have to come before optional ones. **Validate** doesn't catch these or a badly formed command name: the extension saves, the command doesn't appear in Discord, and the bot's log says why.


#### Options

| `type` | The member enters | `i.options.<name>` holds |
| --- | --- | --- |
| `string` (the default) | Text | A string |
| `integer` | A whole number | A number |
| `number` | Any number | A number |
| `boolean` | True or False | A boolean |
| `user` | A member | Their user id, as a string |
| `channel` | A channel | The channel's id, as a string |
| `attachment` | A file | `{ id, filename, size, contentType }`. Read the file with `host.files` (see Files below). |

An optional option the member left out is `null`.

#### Where commands appear

Olisar registers every extension's commands in every server the bot is in, a few seconds after you save, install or delete an extension. Turning an extension on or off doesn't change that: in a server where it's off, the command still shows, and running it only tells the member privately that the extension is off.

A command name belongs to one command. Saving or installing an extension whose command uses the name of one of Olisar's own or another extension's is refused (see [Write an extension](#write-an-extension)).

#### Reply

The handler's `i` holds the command's context and four methods:

| Member | What it is |
| --- | --- |
| `options` | The option values, keyed by option name |
| `guildId`, `channelId` | Where the command ran |
| `userId`, `displayName` | Who ran it |
| `reply(payload)` | Sends the first response. Needs `discord.reply`. |
| `followUp(payload)` | Sends another message after the first. Needs `discord.reply`. |
| `modal(spec)` | Opens a form and resolves with the answers. Needs `discord.modal`. |
| `awaitComponent(opts?)` | Resolves when someone clicks a button or picks a menu option. Needs `discord.components`. |

A payload is a string, or an object with any of these:

| Key | Type | What it does |
| --- | --- | --- |
| `content` | `string` | The message text |
| `embed` | from `host.embed` | A card. See Embeds below. |
| `ephemeral` | `boolean` | `true` makes the message visible only to the member who ran the command |
| `components` | `Component[]` | Buttons and menus. See the sections below. |
| `files` | `FileOut[]` | Files to attach. See Files below. |

The first `reply` is public unless it sets `ephemeral: true`. A `followUp` keeps the first reply's privacy unless it sets `ephemeral` itself, so a private "Checking…" keeps the result private too. Calling `reply` a second time sends a follow-up, and calling `followUp` before any reply sends the first reply.

> [!NOTE]
> **Respond within 3 seconds**
> Discord drops a command that isn't answered in 3 seconds, and Olisar doesn't answer for you. If the handler calls the web or the model before it has anything to say, send a short `reply` first and the result with `followUp`.


```ts
defineExtension({
  id: "mc_check",
  name: "Minecraft check",
  version: "1.0.0",
  permissions: ["fetch", "discord.reply"],
  commands: [
    {
      name: "mccheck",
      description: "Check whether a Minecraft server is up.",
      options: [{ name: "address", description: "server address", type: "string", required: true }],
      handler: async (i) => {
        await i.reply({ content: "Checking " + i.options.address + "…", ephemeral: true });
        const r = await host.fetch("https://api.mcsrvstat.us/3/" + encodeURIComponent(i.options.address));
        const body = await r.json();
        await i.followUp(body.online ? "It's up." : "It's down.");
      },
    },
  ],
});
```

A command run can last up to 15 minutes in all, which leaves room for a member to fill in a form or click a button.

#### Ask with a form

`i.modal(spec)` opens a Discord form and resolves with the answers, keyed by each field's `id`. This one, modeled on the marketplace's Member Directory extension, asks a member what they can help with:

```ts
defineExtension({
  id: "skills",
  name: "Skills",
  version: "1.0.0",
  permissions: ["kv", "discord.reply", "discord.modal"],
  commands: [
    {
      name: "skills",
      description: "Tell the server what you can help with.",
      handler: async (i) => {
        const form = await i.modal({
          title: "Your skills",
          fields: [{ id: "skills", label: "What can you help with?", style: "paragraph", required: true }],
        });
        const dir = (await host.kv.get("dir")) || {};
        dir[i.userId] = { name: i.displayName, skills: form.skills.slice(0, 500) };
        await host.kv.set("dir", dir);
        await i.followUp({ content: "You're in the directory.", ephemeral: true });
      },
    },
  ],
});
```

| Field key | What it does |
| --- | --- |
| `id` | The key the answer comes back under |
| `label` | The question. Cut to 45 characters. |
| `style` | `"short"` (the default) for one line, `"paragraph"` for several |
| `required` | Whether the member has to fill it in. Defaults to `false`. |

The rules:

- A form has to be the command's first response, so open it before any `reply`.
- A form holds up to 5 fields; the rest are ignored. Its `title` is cut to 45 characters.
- The form waits up to 10 minutes for the member to submit it, then the promise rejects. Discord doesn't report a form closed without submitting, so that also ends in the 10-minute rejection.
- Send what comes after with `followUp`.

A button handler can't open a form.

#### Buttons for one reply

For a one-off question, such as a confirmation, send buttons with a `customId` and wait for the click with `i.awaitComponent`. It resolves with the clicked component's `customId`, and for a menu, the chosen `values`:

```ts
defineExtension({
  id: "faq_reset",
  name: "FAQ reset",
  version: "1.0.0",
  permissions: ["kv", "discord.reply", "discord.components"],
  commands: [
    {
      name: "faqreset",
      description: "Delete every saved answer.",
      defaultMemberPermissions: "manage_guild",
      handler: async (i) => {
        await i.reply({
          content: "Delete every saved answer?",
          ephemeral: true,
          components: [
            { kind: "button", customId: "confirm", label: "Delete all", style: "danger" },
            { kind: "button", customId: "cancel", label: "Cancel" },
          ],
        });
        let choice;
        try {
          choice = await i.awaitComponent({ timeoutMs: 60000 });
        } catch (e) {
          await i.followUp("No answer, so nothing was deleted.");
          return;
        }
        if (choice.customId !== "confirm") {
          await i.followUp("Canceled.");
          return;
        }
        await host.kv.delete("faq");
        await i.followUp("Deleted every saved answer.");
      },
    },
  ],
});
```

The rules:

- The first click from anyone who can see the message resolves the wait, and it doesn't say who clicked. Send the buttons with `ephemeral: true` when only the member who ran the command should answer.
- `timeoutMs` defaults to 5 minutes. These buttons stop responding after 5 minutes even if `timeoutMs` is longer, and the promise rejects when it runs out, so catch it.
- They stop working when the bot restarts.
- `customId` buttons work only in a command's replies. For anything else, use persistent buttons.

#### Persistent buttons and menus

A persistent button keeps working for everyone, for as long as the message exists, across bot restarts. Declare its handler in `components` and point the button at it with `handlerId`; `arg` carries a small payload, such as which poll and which option. This poll is a shorter version of the marketplace's Polls extension:

```ts
function card(poll) {
  const lines = poll.options.map((o, n) =>
    o + ": " + Object.values(poll.votes).filter((v) => v === n).length);
  return host.embed({
    title: poll.question,
    description: lines.join("\n"),
    color: poll.open ? 0x5865f2 : 0x99aab5,
    footer: poll.open ? "Click to vote. Click another option to change your vote." : "Closed",
  });
}

defineExtension({
  id: "quickpoll",
  name: "Quick poll",
  version: "1.0.0",
  category: "Community",
  permissions: ["kv", "discord.reply", "discord.components"],
  commands: [
    {
      name: "quickpoll",
      description: "Start a poll with vote buttons.",
      options: [
        { name: "question", description: "what you're asking", type: "string", required: true },
        { name: "options", description: "choices, comma-separated (up to 5)", type: "string", required: true },
      ],
      handler: async (i) => {
        const options = String(i.options.options).split(",").map((s) => s.trim()).filter(Boolean).slice(0, 5);
        if (options.length < 2) {
          await i.reply({ content: "Give at least two options.", ephemeral: true });
          return;
        }
        const id = String(Date.now());
        const poll = { question: i.options.question, options, votes: {}, creator: i.userId, open: true };
        await host.kv.set("poll:" + id, poll);
        const buttons: Component[] = options.map((o, n) => (
          { kind: "button", handlerId: "vote", arg: id + ":" + n, label: o.slice(0, 80) }
        ));
        buttons.push({ kind: "button", handlerId: "close", arg: id, label: "Close", style: "danger" });
        await i.reply({ embed: card(poll), components: buttons });
      },
    },
  ],
  components: {
    vote: async (i) => {
      const [id, n] = i.arg.split(":");
      const poll = await host.kv.get("poll:" + id);
      if (!poll || !poll.open) return i.reply("This poll is closed.");
      poll.votes[i.userId] = Number(n);
      await host.kv.set("poll:" + id, poll);
      await i.update({ embed: card(poll) });
    },
    close: async (i) => {
      const poll = await host.kv.get("poll:" + i.arg);
      if (!poll) return i.reply("This poll is gone.");
      if (i.userId !== poll.creator) return i.reply("Only the person who started the poll can close it.");
      poll.open = false;
      await host.kv.set("poll:" + i.arg, poll);
      await i.update({ embed: card(poll), components: [] });
    },
  },
});
```

Each click runs the handler with its own `i`:

| Member | What it is |
| --- | --- |
| `customId` | The `components` key that was clicked, such as `"vote"` |
| `arg` | The `arg` set on the button or menu |
| `values` | For a menu, a list holding the chosen option's `value` |
| `guildId`, `channelId`, `messageId` | Where the message is |
| `userId`, `displayName` | Who clicked |
| `reply(payload)` | Answers the person who clicked, privately. Needs `discord.reply`. |
| `update(payload)` | Edits the message the button is on. Pass `components: []` to remove the buttons; leave `components` out to keep them. It can't attach files. Needs `discord.components`. |
| `deferUpdate()` | Acknowledges the click with no visible change. Needs `discord.components`. |

The rules:

- A `components` key, and so a `handlerId`, is 1 to 32 lowercase letters, digits and underscores. A button whose `handlerId` breaks that rule shows up but does nothing when clicked.
- `arg` is up to 40 characters. Store anything bigger with `host.kv` and pass its key.
- The extension id, `handlerId` and `arg` together have to fit in 93 characters, or sending the message throws.
- Clicks on one message run one at a time, so reading and writing `host.kv` in a handler doesn't race other clicks on the same message.
- A member who clicks the same message twice within 1.5 seconds is asked to slow down, and the second click doesn't run.
- A handler gets 30 seconds. It can't open a form or wait on another click. If it doesn't reply or update, Olisar acknowledges the click for it.
- In a server where the extension is off, a click only tells the member it's off.

Persistent buttons can also go on messages from `host.discord.send` and from a click's own `reply` (see [SDK reference](#sdk-reference)).

#### Components

| Key | Button | Menu |
| --- | --- | --- |
| `kind` | `"button"` | `"select"` |
| `handlerId` | A `components` key, for a persistent button | Same, for a persistent menu |
| `customId` | An id for `awaitComponent`, for a one-off button | Same, for a one-off menu |
| `arg` | A small payload for the handler | Same |
| `label` | The button text. Persistent buttons cut it to 80 characters. | Not used |
| `style` | `"primary"`, `"secondary"` (the default), `"success"` or `"danger"` | Not used |
| `placeholder` | Not used | The text shown before a choice |
| `options` | Not used | `[{ value, label }]`. The member picks one. |

Give each component a `handlerId` or a `customId`, not both.

#### Embeds

`host.embed(spec)` builds a card to pass as `embed` in a reply, an `update` or `host.discord.send`. It returns at once, so there's nothing to `await`.

| Key | Type | What it shows |
| --- | --- | --- |
| `title` | `string` | The card's title |
| `description` | `string` | The main text, with Discord's Markdown |
| `url` | `string` | Makes the title a link |
| `color` | `number` | The side bar color, such as `0x5865f2` |
| `fields` | `{ name, value, inline? }[]` | Labeled values. `inline: true` sets them side by side. |
| `footer` | `string` | Small text at the bottom |
| `thumbnail` | `string` | An image URL shown at the top right |
| `image` | `string` | An image URL shown full width |

Discord's own embed limits apply, such as 25 fields per card.

#### Files

A command with an `attachment` option gets the file's details in `i.options`, not its contents. Load the contents with `host.files`, which needs no permission:

| Method | Resolves to | Use it to |
| --- | --- | --- |
| `host.files.ingest(optionName)` | `{ blobId, filename, size, contentType }` | Keep the file on the host and pass its `blobId` to `host.fetch` or a reply. Up to 25 MB. |
| `host.files.read(optionName)` | `{ filename, contentType, size, contentB64 }` | Bring the file into your code as base64. Up to 20 MB. |
| `host.files.from({ name, text, contentB64, contentType })` | `{ blobId, filename, size, contentType }` | Turn text or base64 you made into a host-held file. Up to 20 MB. |

`ingest` and `read` work only in slash command handlers, up to 5 times per run. A run can hold up to 8 files on the host, 50 MB in all, and a `blobId` is valid only in the run that made it. The sandbox has no `atob`, so when you're only passing a file along, `ingest` saves you decoding base64 yourself.

This command sends an uploaded file to a web API and replies with the result, without the bytes ever entering your code:

```ts
defineExtension({
  id: "shrink",
  name: "Shrink",
  version: "1.0.0",
  permissions: ["fetch", "discord.reply"],
  commands: [
    {
      name: "shrink",
      description: "Compress a file.",
      options: [{ name: "file", description: "the file to compress", type: "attachment", required: true }],
      handler: async (i) => {
        await i.reply({ content: "Compressing " + i.options.file.filename + "…", ephemeral: true });
        const input = await host.files.ingest("file");
        const res = await host.fetch("https://api.example.com/compress", {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          bodyBlobId: input.blobId,
          responseBlob: true,
        });
        if (!res.ok || !res.blobId) {
          await i.followUp("Compression failed.");
          return;
        }
        await i.followUp({ content: "Done.", files: [{ name: input.filename + ".gz", blobId: res.blobId }] });
      },
    },
  ],
});
```

To attach a file you made, put it in `files`. Each entry has a `name` and exactly one of `text`, `contentB64` or `blobId`:

```ts
await i.reply({ content: "Here you go.", ephemeral: true, files: [{ name: "faq.csv", text: csv }] });
```

A message carries up to 10 files; any past that are dropped. A `text` or `contentB64` file can be up to 20 MB and a `blobId` file up to 25 MB, with 25 MB in all per message.

#### What members see when something fails

Olisar answers these privately, so only the member who ran the command or clicked sees them.

| Situation | The member sees |
| --- | --- |
| The extension is off in this server | A note that the extension is off and an admin can turn it on |
| The command's handler threw or ran past its budget | `that command hit an error.` |
| The command failed some other way, such as Discord refusing the reply | `that command timed out or failed.` |
| A persistent button's extension is off in this server | `that extension is turned off here.` |
| A persistent button's handler threw | `that action hit an error.` |

If the handler already replied, the error arrives as a private follow-up. The cause goes to the bot's log, which the operator reads under **Settings > Logs**.

### Share extensions as files

An `.olx` file carries one extension's source code, so you can move an extension from one Olisar install to another: export it, send the file, and the other operator imports it. The [marketplace](#the-marketplace) shares the same files through a catalog.

Only the operator can export and import extensions.

#### Export an extension

1. On the Extensions tab, select the extension.
2. Press its **Export .olx** button.

The console downloads `<id>-<version>.olx`. It's a JSON file you can open in any text editor:

| Key | What it holds |
| --- | --- |
| `id`, `name`, `version`, `category`, `description` | The extension's details |
| `source` | Your TypeScript, exactly as saved. There's no compiled code in the file. |
| `permissions` | The capabilities your code lists |
| `author` | The Discord user ID of the person who wrote it in the console, and a name if it was imported with one |
| `content_hash` | A fingerprint of the id, version, permissions and source, to catch a damaged or edited file |
| `signature`, `public_key`, `signature_algo` | Your install's signature over that fingerprint, and the public half of the key that made it |
| `olx_version`, `sdk_version` | The file format and SDK version, so an older Olisar can refuse a file it can't read |

> [!WARNING]
> **The file names you**
> Anyone you send the file to can read your source and your Discord user ID in `author`. Publishing to the marketplace makes the same file public.


Your install signs every export with its own signing key, which Olisar creates the first time it needs one. The private half stays in your install's database. Whoever imports the file sees the key's fingerprint, and can recognize the same key on later files from you. [Security & trust](#security--trust) explains what a signature does and doesn't prove.

#### Import an extension

1. On the Extensions tab, press the **Import .olx** button next to **Marketplace**.
2. Press **Choose .olx file…** and pick the file.
3. Read the review screen, described below.
4. Untick any capability you don't want to grant.
5. Tick **I understand this is third-party code and accept the risks of installing it.**
6. Press **Install**.

The extension appears in the list with an **Imported** badge, off in every server. Turn it on where you want it (see [Extensions](#extensions)).

#### The review screen

Before anything is installed, Olisar rebuilds the extension from the file's source and runs it once in the sandbox to see what it declares. The review screen shows what it found:

| Part | What it tells you |
| --- | --- |
| Name, version, category and id | From the code itself. "by" and a name appear when the file carries an author name. |
| Signature | **Signed & verified** with the signer's fingerprint, **Unsigned** (its author and integrity can't be checked), or **Signature invalid**, which blocks the install |
| **What it adds** | Its tools, slash commands, and **Shapes replies** if it adds an instruction to how Olisar answers |
| **Risk assessment** | A 0 to 100 score from your own Gemini model's review of the source, with a one-line summary and the reasons |
| **Capabilities to grant** | Every capability the code asks for, each with a checkbox, all ticked to start |

The capability list comes from running the code, not from the file's `permissions`, so a file can't hide what its code uses. A request for one of the install's keys (`secret:` capabilities) shows unticked and can't be granted: imported extensions never get them. Anything you leave unticked fails when the extension tries it.

The risk review uses your Gemini quota. Opening the same file again reuses the earlier review until Olisar restarts. When it can't run, for example because the quota is used up, the screen says there's no automated review this time. A score is a model's opinion, so read the capabilities either way.

Olisar won't install a file when:

- its signature is invalid
- its `content_hash` doesn't match its contents, which means the file was changed or damaged
- an extension with the same id is already installed
- one of its slash commands or tools uses a name that's taken (see [Write an extension](#write-an-extension))
- it was made by a newer version of Olisar, in which case update Olisar and try again

#### Update an imported extension

An imported extension can't be updated in place. To install a newer file, delete the old one (open it with **Edit code**, press **Delete** and confirm), then import the new file.

Deleting an extension deletes its stored data and its settings in every server, and you grant its capabilities again on import. If the extension came from the marketplace, use its update button instead (see [The marketplace](#the-marketplace)).

### The marketplace

The marketplace is a shared catalog of extensions that anyone running Olisar can install from and publish to. You browse, install and publish from the console, and Olisar talks to the marketplace for you.

Only the operator can use the marketplace. Installing something there doesn't turn it on anywhere: admins still choose which servers it runs in.

#### Browse and install

1. On the Extensions tab, press **Marketplace**.
2. Type in the search box and press **Search**, or browse the list as it opens.
3. Press **Install** on the extension you want. Olisar runs its own security review of the code first, so the button reads **Reviewing…** for a few seconds.
4. On the **Install from marketplace** screen, check what it adds and what it can access, untick any capability you don't want to grant, and tick the box accepting the risk.
5. Press **Install**.

The list shows up to 30 extensions, most installed first, and search matches an extension's id and description. Each entry shows its version, category, publisher and the capabilities it asks for. A publisher shown in green with a check mark has verified with Discord.

The install screen is the same review screen an `.olx` import uses, described in [Share extensions as files](#share-extensions-as-files). The installed extension gets a **Marketplace** badge and starts off in every server.

#### Update an installed extension

When you open the Extensions tab, the console checks the marketplace for newer versions of what you installed from it. An extension with one shows **Update available**.

1. Select it and press **Update to v** followed by the new version number.
2. On the review screen, check what changed and choose what to grant again.
3. Press **Install**.

An update has to be signed by the same key as the version you have. If the publisher's key changed, Olisar refuses the update; delete the extension and install it again if you trust the new key.

#### Report an extension

If a marketplace extension misbehaves, press the flag button (**Report this extension**) on its marketplace entry or on its panel. Describe what happened under **What went wrong?**, optionally press **Add attachments** (up to 8 files of 3 MB each) or **Add bot logs**, and press **Send report**.

The report goes to the Olisar team with your Discord user ID. **Add bot logs** attaches the last 800 lines of your bot's log, so read them first if your log might hold something private. To stop the extension right away, see [Security & trust](#security--trust).

#### Publish your own extension

You can publish an extension you wrote in the console, one with the **Custom** badge.

1. Select the extension on the Extensions tab and press **Publish**.
2. The first time, choose a publisher handle: 2 to 64 characters of lowercase letters, digits, `_` and `-`. Press **Register**.
3. Olisar reviews your source with your own Gemini model and shows a risk score from 0 to 100.
4. If it says **Review passed**, press **Publish**.

A notice with a **Stop** button stays up while Olisar runs the review again, which takes about a minute, and then uploads your extension. Press **Stop** while the review is still running and nothing is published. When it's done, the panel shows a **Published** badge and the listing's address, `<handle>/<id>`.

A score of 70 or more (the default threshold) shows **Publish blocked** with the reasons, and you can't publish until a review passes. If the review can't run, for example because your Gemini quota is used up, publishing is blocked until it can.

Your handle belongs to your install's signing key, the one that signs your `.olx` exports. Only that key can publish under the handle, and every version you publish is signed with it.

> [!WARNING]
> **Everything you publish is public**
> Anyone can download a published extension. The file holds your full source and your Discord user ID.


The hosted marketplace takes `.olx` files up to 1 MB each, up to 30 new versions a day (counted in UTC), and up to 100 MB per publisher.

#### Publish an update

A published version never changes, so a new release needs a new `version` in your code.

1. Edit the extension, raise its `version`, and save. The panel shows **Unpublished changes**.
2. Press **Push update** and follow the same review as the first publish.

If you press **Push update** without changing the version, the console tells you to bump it. When the marketplace already has your current code, the button reads **Re-publish**, and publishing it again changes nothing. People who installed your extension see **Update available** the next time they open their Extensions tab.

#### Verify with Discord

Verifying links your publisher handle to your Discord account, and gives your listings the verified check mark.

1. On the Extensions tab, press **Marketplace**.
2. In the bar that reads **Publishing as** and your handle, press **Verify with Discord**.
3. Your browser opens Discord's sign-in. Sign in and allow it; it shares only who you are.
4. The marketplace's page names your Discord account and your handle. If both are right, press **Link to** followed by your handle.

The link lasts 10 minutes. The console notices within a few seconds and shows **Discord-verified**. The sign-in runs on the marketplace's own Discord app, so there's nothing to set up on your bot.

The badge means the marketplace can hold a real Discord account responsible for those extensions, and a ban follows that account. It doesn't mean anyone reviewed the code.

#### Change your handle

Press **Change handle** in the same bar and enter the new one. Verification carries over. Extensions you've already published stay listed under the old handle, and new ones publish under the new handle, so pick a handle before your first publish if you can.

#### Remove an extension from the marketplace

Removing an extension is called yanking it.

1. In the marketplace list, find your extension and press **Yank**.
2. Type `yank <handle>/<id>` to confirm.

Yanking takes every version of the extension off the marketplace. Anyone who installed it keeps it: the next time they open their Extensions tab, its badge changes from **Marketplace** to **Imported**. It keeps working with the capabilities they granted, gets no more updates, and stays under the same limits as any extension from someone else.

A yanked version can't be published again. To list the extension again, publish it under a new version number; the yanked versions stay off. An extension the Olisar team removed can't take new versions at all.

#### Use a different registry

The marketplace runs on a registry server. Olisar uses the hosted one at `https://olisar-registry.gabrielyp.workers.dev` unless the `OLISAR_REGISTRY_URL` environment variable says otherwise. Set it where Olisar's backend reads its settings, such as the `.env` file of a server install, then restart Olisar.

The registry is a Cloudflare Worker in the `registry` folder of Olisar's source code, and its README covers deploying one. For **Verify with Discord** to work on your registry, it needs its own Discord application, which the README also covers; until then, verifying fails. The files and their signatures are the same on every registry, so installs from yours are checked the same way.

Olisar checks for updates on whichever registry it's set to. After a switch, an extension you installed from the old registry becomes an **Imported** extension the next time you open the Extensions tab, unless the new registry lists it under the same handle and id.

### Security & trust

Extensions run code inside your bot, so Olisar keeps each one in a sandbox and lets it do only what it's been allowed to. This page explains what an extension can and can't reach, so you can decide which ones to install and turn on.

The operator installs extensions and decides what each one may do. Admins decide which installed extensions run in their server.

#### Where an extension comes from

How much an extension may do depends on who wrote it. Built-ins and extensions the operator writes in the console are the operator's own code. Imported and marketplace extensions are someone else's, and they stay that way, even after their publisher removes them from the marketplace.

| What it can do | Built-in or written in the console | Imported or from the marketplace |
| --- | --- | --- |
| Use capabilities | Every one its code lists | Only the ones the operator ticked when installing |
| Use the install's API keys | If its code asks | Never |
| React when a member joins | Yes | Never |
| Have Olisar write with a channel's recent conversation in view | Yes | Never |
| Ping people in its posts | The members it mentions | Nobody |
| Start on in every server | If its code asks | Never |

No extension can ping `@everyone`, `@here` or a role.

#### The sandbox

Extension code runs in a separate process that starts without your bot token or API keys, and it has no access to the files on the machine. It reaches the outside world only through a fixed set of functions Olisar provides. Apart from writing to the bot's log, reading its own settings and opening files members upload to its commands, each one needs a capability.

It can make web requests only with the `fetch` capability, and only to public addresses. It can't reach the machine Olisar runs on or anything else on your home or office network, such as a router or a file server.

Each run has a time and memory limit, and Olisar stops code that passes it, so a stuck extension can't hang the bot. Every run starts fresh. What an extension keeps between runs goes in its own storage, which is separate for each server and which no other extension can read.

#### Capabilities

An extension lists the capabilities it wants. For one you install, that list is a request: the install screen shows each capability with a checkbox, and the extension gets only the ones left ticked. Whatever you untick fails when the extension tries it, and its panel lists it under **Requested but not granted**.

| The console shows | Name | What it means for you |
| --- | --- | --- |
| Make web requests to any public URL | `fetch` | It can send anything it has seen to any website |
| Use its own private key-value storage | `kv` | Storage only it can read |
| Add sources to the knowledge base | `kb.write` | Olisar will read the pages it adds and answer from them |
| Add glossary / memory facts | `glossary.write` | Olisar will treat what it adds as facts about your server |
| Reply in Discord | `discord.reply` | It can answer its own commands and buttons |
| Show pop-up forms (modals) | `discord.modal` | It can ask the member who ran its command to fill in a form |
| Use buttons and select menus | `discord.components` | It can wait for clicks on its buttons and menus and edit the message they're on |
| Post messages to your channels (no @mentions) | `discord.send` | When Olisar uses one of its tools, it can post in any channel of that server the bot can post in, up to 5 messages a minute |
| Generate text with your AI model (uses your quota) | `model.generate` | It spends your Gemini quota |
| Use the "…" secret key | `secret:` | Reads one of the install's API keys. Never available to an installed extension, so the install screen shows it unticked and locked. |

You can't change what you granted from the console afterward. A marketplace update asks again; for anything else, delete the extension and install it again, which deletes its stored data.

#### What an extension sees

An imported or marketplace extension has no way to read messages, member lists, the knowledge base, the glossary or what Olisar remembers. It sees only what's handed to it:

- what Olisar passes its tools during a conversation, which can include what members said
- the options and form answers members give its slash commands
- the user ID and display name of whoever used it, and the server and channel IDs

An extension that can also `fetch` can send any of that to a website. One that shows **Shapes replies** also adds its own instructions to Olisar's, in every server where it's on, and those can steer what Olisar passes its tools.

#### Signatures

Every Olisar install has its own signing key, and it signs the extensions it exports or publishes. The signature covers the extension's id, version, capabilities and source. When you install one, the install screen shows one of three results:

| Result | What it means |
| --- | --- |
| **Signed & verified** | The code hasn't changed since it was signed by the key with the fingerprint shown |
| **Unsigned** | There's no way to tell whether anyone changed it. Anyone can strip a signature from a file, so treat an unsigned file as unverified. |
| **Signature invalid** | It was changed after it was signed. Olisar won't install it. |

A valid signature tells you the code is what that key signed, not who holds the key: anyone can make one. The fingerprint is how you recognize the same signer across files. The author name a file shows isn't covered by the signature, so it proves nothing. Once you've installed from the marketplace, Olisar accepts updates only when they're signed by the same key.

Olisar never runs prebuilt code from a file or the marketplace. It compiles every extension you install from its source and reads the capabilities from running that code, so a file can't hide what it asks for.

#### What the marketplace checks

The marketplace ties each publisher handle to one signing key and accepts new versions under that handle only when that key signed them. A published version never changes.

It doesn't review the code. The publisher's own Olisar runs an AI review before publishing and blocks code that scores as high risk, but that review runs on the publisher's side, so it isn't a guarantee. When you open the install screen, your Olisar runs its own review with your Gemini model:

| Score | Reading |
| --- | --- |
| 0 to 30 | Low risk |
| 31 to 69 | Some concerns |
| 70 to 100 | High risk |

The score comes with a summary and reasons. It's a model's opinion: it can miss things, and it reads only the first 24,000 characters of the source.

A publisher with the verified check mark has linked a Discord account, which the marketplace can hold responsible and ban. That says who's accountable, not that the code is safe. See [The marketplace](#the-marketplace).

#### Decide whether to install one

- Compare what it asks for with what it does. A dice roller needs no capabilities; a game-server status check needs `fetch`.
- Take extra care with `fetch` on an extension whose tools or commands handle what members write, since it could send that anywhere.
- Look at who signed it: a verified publisher, or a fingerprint you've trusted before.
- Read the risk assessment and its reasons.
- Grant the least that makes it work. Untick anything it doesn't need for what you want it to do.
- Turn it on in one server first.

Installing doesn't turn an extension on anywhere, so the operator can install it, read its code with **Edit code**, and delete it without it ever running in a server.

> [!NOTE]
> **Editing an installed extension keeps your choices**
> If you change an installed extension's code with **Edit code** and save it, the capabilities you unticked at install stay off. A capability your edit adds is granted, as it is for code you write yourself. The install's API keys stay off-limits either way.


#### If an extension misbehaves

- Run `/killswitch` in Discord to turn it off in that server at once (see [Slash commands](#slash-commands)).
- Turn it off on the Extensions tab in any other server where it's on.
- Delete it to remove its code and its stored data.
- If it came from the marketplace, report it with the flag button on its panel (see [The marketplace](#the-marketplace)).

## Reference

### Usage & rate limits

The Usage tab shows how much of today's free Gemini allowance the bot has left (or, when the key has billing on, what it has spent), which model is answering, and where the requests went. Any admin can open it.

Google's daily limits reset at midnight Pacific time, and the tab shows that moment in your own time zone. The counts cover every request the bot makes, in every server it's in. The fallback chain listed is the selected server's: its **Primary model** on the Behavior tab and every model below it. The order and the models themselves are on [Models](#models).

#### Left today

The top panel says whether Olisar can keep replying until the reset.

| Figure | What it shows |
| --- | --- |
| **Left today** | Requests left across every model in the chain, out of their combined daily limits, and how long until the reset |
| **Replying with** | The first model in the chain that can take a request right now, or **No model left** |
| Pace line | Whether today's rate of use lasts until the reset, or roughly when it runs out |
| **Memory search** | What's left of the daily limit for looking things up by meaning, which has a model of its own |
| **Web search** | What's left of Google's daily allowance for web searches. A server can stop sooner, at its **Web searches per day** setting |

When memory search runs out, Olisar keeps replying but can't recall older messages, summaries, remembered facts or knowledge-base passages until the reset, and message search matches words only.

A model Google has turned away for the day counts as zero, even if Olisar counted fewer requests than its limit. Google doesn't publish free-tier daily limits, so each limit starts as an estimate and switches to Google's own number the first time Google turns that model away.

#### With billing on

A key with billing on has no daily allowance to count down, so the panel shows **Spent this month** instead, with "of your $50 budget · on pace for $38", or "On pace for $38 by the end of the month" when there's no budget. The pace line warns from 80% of the budget, and when the month is on pace to go over it. **Free web searches** counts the month's searches against the 5,000 Google includes, and **Memory search** shows a plain count for today.

The money is Olisar's estimate, from Google's standard paid prices as of October 2026, and Google's own bill is what you pay. Thinking tokens count as output, as Google bills them. The embedding model behind memory search doesn't report tokens, so its cost assumes about four characters a token. Usage recorded before Olisar split input from output tokens is priced as input, so those days read a little low. A month is a calendar month on Pacific time, as Google bills.

The **Live** badge means the figures are current. If the console loses contact with the bot, the badge reads **Not responding**, the figures dim, and a banner says they're the last ones the bot reported.

#### Fallback chain

One row per model, in the order Olisar tries them, with what's left of each model's daily limit.

| Status | Means |
| --- | --- |
| **Replying** | The first model that can take a request |
| **Standby** | Further down the chain, waiting its turn |
| **Back in** 0:48 | Resting after a per-minute limit or a brief error from Google. It comes back on its own |
| **Used up** | Out for the day, with the time it ran out. Olisar skips it until the reset |

A hatched stretch on a used-up model's meter is quota Google says is gone that this bot never used. The limits belong to the key's Google Cloud project, so another app or bot using a key from the same project draws on them too.

With billing on, the last column is **Today**: what each model has cost today, and how many requests it took.

#### By feature

Where the requests went today, or over the last 7 or 30 days: **Replies**, **Summaries**, **Impressions**, **Glossary**, **Image** and **Everything else**, which lists its parts underneath (chiming in, web search, catch-up, extensions and a few smaller ones). **Image** is Olisar describing posted images. Images Gemini makes show under **Everything else** as making images; Cloudflare's aren't counted here. Memory search isn't counted either, since it has its own limit.

#### Stats

Four tiles: **Requests today** and **Tokens today** against the same time yesterday, the **Busiest minute** against that model's per-minute limit, and when every model **Last ran out**. Under them, **Requests per day** charts the last 14 days against the chain's combined daily limit (the dashed line), with the days it ran out in amber.

With billing on, **Spent today**, with yesterday's spend under it, takes the place of **Last ran out**, and the chart is **Spend per day**, with no limit line.

#### When every model is used up

Olisar keeps reading and storing messages but can't write replies until the reset.

- Anyone who addresses it gets the **When rate-limited** reply, "i'm a bit rate-limited right now — give me a minute and try again?", which you can reword on Command replies.
- The bot status at the bottom of the sidebar reads **Rate-limited**. It also shows this for a moment when every model is resting at once.
- The sidebar shows **Out of requests**, back at midnight Pacific, with what the day would cost with billing on. The pace line on Usage says the same: "With billing on, today would cost about $1.20." Both link to Google AI Studio with **Turn on billing**, the sidebar only for the operator.
- Summaries, impressions and the glossary wait and catch up after the reset. Images posted in the meantime don't get a description, so search finds them by file name only.
- Once an hour, Olisar asks Google again about each used-up model. If you turn on billing for the key's Google Cloud project, the next of those requests goes through, and Olisar checks the key again and switches to billing on (see [Models](#models) for what billing changes).

A used-up model is tied to the key it ran out on. A Gemini key from a different Google Cloud project, pasted on the [API keys](#api-keys) tab, has its own limits, and the models take requests again at once.

#### Make the allowance last

1. Open **By feature** and find what's spending the most.
2. On Behavior, turn off **Speak up on its own** and **React with emoji** if they're on. Both read the conversation to decide whether to join in.
3. Raise **Summary token threshold**, **Glossary mine threshold** and **Persona rebuild (messages)** so background work runs less often.
4. Lower **Web searches per day**. Each web search also spends a request from Gemini 2.5 Flash or 2.5 Flash-Lite.
5. If **Image** is large, set busy image channels to **not indexed** on Channels. Every image posted in an indexed channel gets a description. A channel set to not indexed also drops out of message search, and what's already indexed there is erased.

Summaries, impressions, the glossary, catch-ups and image descriptions run on the Flash-Lite models at the bottom of the chain, the same ones replies fall back to when the top models are used up. With billing on, the same steps lower the bill.

#### Limits that apply before the quota runs out

Olisar also limits how fast it can be used, whatever is left today.

| Limit | Default | When it's reached |
| --- | --- | --- |
| Replies to one member | 8 in a row, then 1 every 15 seconds | In chat, Olisar sends the **When rate-limited** reply once and ignores further messages until the member can have a reply again. `/ask` and `/catchup` show it privately each time |
| Replies in one server | 30 in a row, then 1 every 4 seconds | The same. All DMs share one budget |
| Requests per model per minute | Set per model, see [Models](#models) | The model shows **Back in** and the next one answers |
| Web searches per server | 100 a day, or 3,000 a month with billing on, set on Behavior | Olisar answers from what it already knows. Each server counts only its own searches |
| Images per reply | 2 | Olisar says how many it made, and that you can ask for more in another message |

### Privacy & data

Olisar keeps what it learns about your server in a database on the machine it runs on, and sends the text it works with to Google Gemini. Read this before you turn Olisar on in a server, so you can tell members what it keeps and how to remove it.

#### Where it's stored

Each bot has its own database, on the operator's computer or on the VM that hosts it. [Hosting & your data](#hosting--your-data) has the folder. There's no Olisar cloud copy. Admins who sign in to the console read and change that database live, but only the operator has the file itself.

> [!WARNING]
> **Backups keep deleted data**
> Before each update, Olisar copies the database beside it as `olisar.db.pre-<version>` and keeps the last two. Moving a bot between your computer and a VM leaves the old copy behind too. Deleting data with `/forget-me`, the member portal or **Clear memory** doesn't reach these copies, so delete them by hand when a deletion has to be complete.


#### What Olisar stores

| Data | What it holds | `/forget-me` |
| --- | --- | --- |
| Conversation memory | Messages from channels set to `memory` or `both`, and from DMs, with the author's display name. Includes embed text, file names and image descriptions | Deleted |
| Search index | A copy of every message in every channel Olisar can read, including channels set to `off`, unless the channel is set to **not indexed**. It's on from the start and reads back through history on its own. DMs too. See [Memory & search](#memory--search) | Deleted |
| Image descriptions | A short description of each image a member posts in an indexed channel, stored with the message. The image itself isn't kept | Deleted |
| Channel summaries | Rolling summaries of conversation memory, which can name members | Kept |
| Member profiles | Display name, avatar, roles and join date for every member of every server Olisar is in, whether they've talked to it or not | Kept |
| Impressions | A short characterization of a member, written by the model from their messages | Cleared |
| Remembered facts | Things Olisar chose to remember about a member | Deleted |
| Reminders | Reminders a member asked for, and ones Olisar set from a date they mentioned | Deleted |
| Glossary | The server's own terms and lore, learned from conversation | Kept |
| Reference snapshots | Recent messages from channels set to `resource` or `feed` | Kept |
| Knowledge base | Pages, sites and files admins added | Not member data |
| Blank-reply reports | The prompt and a snapshot of the bot's logs, saved when a reply comes back blank while remote access is on | Deleted |
| Activity record | The last 50 statuses Olisar set itself and the last 50 prompts for images it generated | Kept |
| Activity log | Changes admins make in the console or by asking Olisar in chat, and member-portal actions with the member's IP address. Only the operator can read it | Kept |
| Sign-ins | Discord ID, username and servers of each admin and member-portal user | Kept |
| Usage counts | Requests and tokens per model per day, and web searches per server per day, with no content | Not member data |
| Settings and keys | Configuration, the Discord bot token, the Gemini and Cloudflare keys, and the Tailscale auth key | Not member data |

Admins of a server see its members' profiles, impressions and remembered facts on the Members tab. Members who opted out don't appear there.

When someone edits or deletes a message in Discord, Olisar updates or removes its copy in conversation memory, the search index and reference snapshots. Summaries, facts and glossary entries already drawn from that message stay.

A blank-reply report link works for 7 days. Expired reports are deleted the next time any reply comes back blank.

#### Direct messages

Olisar stores and indexes DMs unless the member turns that off with `/dm-indexing enabled:false`. A DM's messages and summaries are only used inside that same DM: they never come up in a server, in a search run from a server, or for anyone else, admins included. The console shows how many DM messages there are, never their text.

Facts Olisar remembers during a DM are the exception. They're filed under the bot's main server (the one it was added to during setup), so they show on that server's Members tab and member portal, and can come up when the same member talks to Olisar there. The operator holds the database file, which contains the DMs themselves.

#### What's sent to Google Gemini

Gemini writes every reply and does Olisar's background work, so the text it works with goes to Google.

| When | What goes to Google |
| --- | --- |
| Olisar replies, or someone runs `/catchup` | The message and any images on it, recent messages in that channel or DM, what it recalls (summaries, older messages, remembered facts, the glossary, knowledge-base passages, reference snapshots), and whatever a tool looks up for the reply, including message-search results |
| Background work | Conversation memory, to write summaries, impressions and the glossary. Conversation memory, summaries, remembered facts and the knowledge base, to index them by meaning |
| An image is posted | Each image a member posts in an indexed channel, to write its description |
| An image is requested, with billing on | The prompt Olisar writes for it, when Gemini makes the image |
| A name is used | A message that mentions Olisar's name without clearly talking to it, to decide whether to answer |
| Optional features | Recent messages, when **Speak up on its own** or **React with emoji** is on. A member's live status or voice channel, when **Status & voice awareness** is on. Web search queries |
| An admin asks | Messages from the search index, when an admin presses **Create impression** on the Members tab or **Deep mine from index** on the Knowledge tab |

The rest of the search index stays on the machine. Text from a channel set to `off` reaches Google only when one of the lookups above turns it up, while images posted there are sent to be described.

#### Free tier and billing

On the free tier, Google's [Gemini API terms](https://ai.google.dev/gemini-api/terms) let it use what Olisar sends, and what Gemini answers, to improve its products, and human reviewers may read it. Google says it disconnects that data from your account before review, and asks that nothing sensitive, confidential or personal be sent to the free tier.

With billing on, Google doesn't use any of it to improve its products, and keeps logs only for a limited time to catch abuse. The setup wizard points this out when the key it checks is on the free tier. If the operator is in the European Economic Area, Switzerland or the United Kingdom, Google applies the paid terms to free requests too. [Models](#models) covers turning on billing.

#### What's sent to Cloudflare

If you add Cloudflare keys on the [API keys](#api-keys) tab, the prompt for each image Cloudflare makes goes to Cloudflare Workers AI. With billing on, Gemini makes images unless you turn that off, and Cloudflare gets a prompt only when Gemini can't make the image. Olisar writes the prompt from the request, so it can include what the member asked for. Workers AI receives nothing else. Cloudflare's [data usage terms](https://developers.cloudflare.com/workers-ai/platform/data-usage/) say it doesn't train its Workers AI models on what you send.

#### Other services

| Service | What it receives |
| --- | --- |
| The Olisar team | Feedback and blank-reply reports you choose to send, with recent bot logs if you include them. The logs name members and channels. Marketplace browsing, installs and publishing. Each signed-in admin's Discord ID, checked against marketplace moderation |
| GitHub | Update checks |
| Tailscale | Console traffic, while [remote access](#remote-access) is on |
| Extensions | Whatever their granted permissions allow. See [Security & trust](#security--trust) |

#### What Olisar doesn't collect

- It never joins a voice channel or hears audio.
- A member's status, activity or voice channel is read live when someone asks, only with **Status & voice awareness** on, and never stored.
- Files members post aren't saved. Olisar keeps their names, and a description for images.
- Console sign-in asks Discord for your identity and server list only, never your email address.
- Nothing said in the test chat on the [Persona](#persona) tab is saved.
- Channels Olisar's role can't see aren't read at all.
- No analytics or tracking is sent anywhere.

#### Member controls

Members run these slash commands in any server Olisar is in. They don't work in a DM with Olisar, where a member can ask it in conversation to stop saving their DMs instead.

| Control | What it does | What it covers |
| --- | --- | --- |
| `/privacy` | Shows the member a private summary of what Olisar keeps, with a link to the member portal if it's open. Works whatever the [access rules](#access-control) say | Changes nothing |
| `/forget-me` | Deletes the member's messages, search-index entries, remembered facts, reminders and blank-reply reports, and clears their impression | Every server Olisar is in now, and DMs |
| `/forget-me stop_remembering:true` | The same, then stops storing or indexing anything they write. Olisar still answers them | Every server, including ones Olisar joins later, and DMs |
| `/dm-indexing enabled:false` | Stops saving and indexing their DMs. What's stored stays until `/forget-me` | DMs |
| [Member portal](#member-portal) | Shows and downloads what's stored, deletes single facts, cancels reminders, pauses recording for 24 hours or 7 days, switches recording and search off, and erases everything | The server it's opened from. DM saving covers all of them |

`/forget-me` leaves the member's profile (name, avatar, roles, join date), Olisar's own replies to them, and any summaries, glossary entries and reference snapshots that mention them. The portal's **Erase** covers only the server it's opened from, so DMs stay.

After `stop_remembering`, a member can be remembered again in a server only by switching **Remember me here** back on in the member portal. Without the portal, the opt-out has no off switch.

#### Admin controls

- On Channels, a channel's mode decides whether Olisar keeps conversation memory there, and **not indexed** keeps it out of the search index and erases what's already indexed. See [Channels](#channels).
- **Clear memory**, at the bottom of the Knowledge tab, erases what Olisar has learned about one server: conversation memory, summaries, the search index, remembered facts, the glossary, member profiles and impressions, and the knowledge base. Settings, opt-outs and DMs stay. It can't be undone.
- When Olisar leaves a server, that server's data stays in the database, and neither `/forget-me` nor the console can reach it any more. Run **Clear memory** before you remove Olisar if you want it gone.

### Troubleshooting

Find what you're seeing below for the likely cause and the fix. When nothing here matches, the operator can read the bot's logs under **Settings > Logs** and send them to the Olisar team from **Settings > Feedback** (see [Console settings](#console-settings)).

#### Olisar doesn't reply

#### Olisar doesn't reply in a channel

Every channel starts set to `off`, and Olisar only talks in channels set to `respond` or `both`. On the Channels tab, set the channel to one of those and press **Save changes**, or run `/olisar watch` in the channel. Threads and forum posts follow their parent channel's mode. See [Channels](#channels).

If the mode is right, check these:

| What you notice | Cause | Fix |
| --- | --- | --- |
| It answers some members but not others | A role is marked allowed on Access, which locks out everyone without one, or the member has a blocked role. In chat Olisar ignores them without saying so | Change the roles on Access. See [Access control](#access-control) |
| It ignores "olisar was down again" but answers "olisar, is it down?" | **Only when addressed** is on, so a message that only mentions its name doesn't count | @mention it, reply to it, or talk to it directly. To answer every message with its name, turn **Only when addressed** off on Behavior |
| It never answers in that one channel, whatever the mode | Olisar's role can't see the channel or send messages in it, and it fails without a message | Give Olisar's role **View Channel** and **Send Messages** there in Discord |
| The message came from another bot | Olisar never answers bots | Nothing to fix |

#### Olisar doesn't reply anywhere in a server

In a new server, Olisar waits for the operator's approval unless it's the main server, the first server Olisar joined, or a server the operator owns. Until then Olisar says nothing there, and its slash commands answer "This server is waiting for the bot's operator to approve it." The operator sees "Olisar was added to" the server's name at the top of the console and presses **Approve**. See [Servers](#servers).

In a server that's already approved, check the **Get started** list under the server switcher. **Choose reply channels** means no channel is set to reply yet.

#### Slash commands are missing

Olisar registers its commands in each server, not in DMs, so none of them appear in a DM with it. In a server waiting for the operator's approval they appear once it's approved. `/olisar` and `/killswitch` only show for members with **Manage Server**. See [Slash commands](#slash-commands).

#### Olisar doesn't reply to DMs

**Reply in DMs** is off on Behavior. DMs follow the main server's Behavior and Access settings, so change it there. All DMs also share one reply budget, so a busy DM inbox hits the rate limit sooner (see [Usage & rate limits](#usage--rate-limits)).

#### Olisar says it's rate-limited

The reply "i'm a bit rate-limited right now — give me a minute and try again?" has three causes. Open the Usage tab to tell them apart.

- If **Left today** is 0 and **Replying with** reads **No model left**, every model is used up for the day. Olisar replies again after the reset at midnight Pacific time, and the bot status at the bottom of the sidebar reads **Rate-limited** until then.
- With billing on, if the sidebar shows **Budget spent** and **At the budget** is **Stop until next month**, Olisar stops until the month ends. Raise the budget on API keys to start it again sooner (see [API keys](#api-keys)).
- Otherwise a member, or the server as a whole, sent messages faster than Olisar's reply limits allow. It clears within seconds.

[Usage & rate limits](#usage--rate-limits) covers both limits and how to make the daily allowance last.

#### Olisar says its mind went blank

"…my mind just went blank there. mind rephrasing?" means the request to Gemini failed for a reason other than the rate limit, or came back with nothing usable. If it happens on every message, the Gemini key is the likely cause: the operator checks the API keys tab, where a missing key shows **Not set** and a key Google refuses is flagged under the field. See [API keys](#api-keys).

Otherwise, ask again or rephrase. With [remote access](#remote-access) on, the blank reply carries a **Report this** button for the person who got it. It opens the Feedback form with the report filled in and the bot's logs from that moment attached. An admin lands in the console; a member lands in the [member portal](#member-portal), so for members it works only when the portal is open. The link expires after 7 days.

#### A feature stops working

#### Web search stopped working

Olisar answers from what it already knows when web search isn't available. Check, in order:

1. **Web search** is on, on Behavior.
2. This server hasn't reached its **Web searches per day** (or **Web searches per month**, with billing on). The count is this server's searches only.
3. On a free key, Google's own web-search allowance isn't spent. If **Web search** on the Usage tab shows 0 left, it's back after midnight Pacific time, and raising the setting won't help.
4. With billing on, the month's budget isn't spent. Web search stops when it is, whatever **At the budget** says.

#### Olisar says it can't make images

With billing on and **Make images with Gemini** on, Gemini makes images until the month's budget is spent. Otherwise, image generation needs a Cloudflare API token and account ID on the API keys tab. If they're there, look under them for "Cloudflare didn't accept that token." or "That token can't use this account." A token that works but still makes no images usually means Cloudflare's free daily allocation is used up, and it comes back when Cloudflare resets it. A refused token and a used-up allocation look the same to a member, so check the keys first. See [Images](#images).

#### Olisar doesn't use a knowledge source you added

Find the source on the Knowledge tab. **Queued**, **Reading** and **Indexing** mean it's still being read. **Error** shows the reason under it:

| Reason | Means |
| --- | --- |
| `no content could be extracted` | The page needs JavaScript to show its text, isn't an HTML page, or the site's robots.txt blocks it |
| `couldn't read it this time` | A re-read failed. Olisar keeps the passages from the last good read |
| `not read: <host> points at a private or local address` | Olisar only reads public addresses |

Press **Retry** once the cause is fixed, or upload the content as a file with `/olisar learn-doc` (PDF, DOCX, TXT or MD, up to 10 MB). A source with no badge has been read, but its passages are only found once Olisar has indexed them by meaning, which waits while memory search is used up on the Usage tab. See [Knowledge base & glossary](#knowledge-base--glossary).

#### Olisar can't find a message you know was posted

| Cause | Fix |
| --- | --- |
| The person asking can't open that channel. Search only returns what they can see | Ask from an account that can |
| The channel is set to **not indexed** on Channels | Set it to **indexed** and press **Save changes**. Olisar reads the history back |
| It was posted while Olisar was offline, or in a channel Olisar couldn't read then | Press **Re-index all** on the Knowledge tab, or run `/olisar reindex` |
| Olisar is still reading the channel's history after joining | Wait. The search index card on the Knowledge tab shows each channel's progress |
| The author turned off search or opted out | Nothing to fix |

See [Memory & search](#memory--search).

#### Olisar won't change its settings when asked

Only admins with **Manage Server** and the operator can have Olisar change its own settings in chat. For them, **Require the PIN** on the Access tab is on by default, and with no PIN set Olisar refuses every change; the Access tab says "No PIN is set, so Olisar refuses these until one is." The operator sets a PIN under **Settings > Security**, or an admin turns the requirement off. See [Access control](#access-control).

#### An extension's command says the extension is off

The extension is turned off in this server, either on the Extensions tab or by `/killswitch`. Turn it back on from the Extensions tab. See [Extensions](#extensions).

#### Olisar says every member is offline

Reading a member's status needs Discord's **Presence Intent**, which **Status & voice awareness** doesn't turn on by itself: it has to be switched on for the bot in the Developer Portal, and Olisar started with `OLISAR_ENABLE_PRESENCE_INTENT=1`. Without it every member reads as offline. Who's in voice works either way. See [Behavior](#behavior).

#### The bot and the console

#### The bot status says Can't connect

Discord refused the bot. Open the drawer at the bottom of the sidebar; the hint under **Can't connect** names the reason.

| Hint | Cause | Fix |
| --- | --- | --- |
| **intents off, tap to fix** | The bot's **Message Content Intent** or **Server Members Intent** is off | Tap the power button beside it, and Olisar turns them on and reconnects. Discord doesn't allow that for an app in 100 or more servers: turn them on under **Bot > Privileged Gateway Intents** in the [Developer Portal](https://discord.com/developers/applications), then tap again |
| **tap to try again** | Discord couldn't be reached, or the bot stopped with an error | Tap to reconnect. If the console then says "Discord rejected the bot token", the token was reset in the Developer Portal. Reset the bot's configuration under **Settings > Bots** and set it up with the new token. Memory and settings stay; the credentials and API keys have to be entered again |

Only the operator can reconnect the bot. See [Create your Discord application](#create-your-discord-application).

#### The bot shows as offline

**Bot offline** with **tap to power on**, in the drawer at the bottom of the sidebar, means the bot was powered down; the operator taps the power button to start it. If the console won't load at all, or the drawer reads **Bot status unknown**, the machine Olisar runs on is asleep or off, or Olisar was quit from the menu bar or tray. Closing the window doesn't stop it. See [Hosting & your data](#hosting--your-data).

#### A bot couldn't start

After three failed starts in a row, the console says the bot couldn't start and shows the end of its output. Your other bots keep running. Press **Try again**. If it fails the same way, the output usually names the cause; if it doesn't, send it to the Olisar team from **Settings > Feedback**. See [Running multiple bots](#running-multiple-bots).

#### The server panel says Unreachable

The desktop app can't reach the VM over SSH. Check that the VM is running, then press **Reconnect**. If the VM was set up somewhere else, add this app's SSH key to it from **Can't connect? Add this app's SSH key to the VM**. See [Host on a server](#host-on-a-server).

#### Signing in

#### Sign-in fails

| What you see | Cause | Fix |
| --- | --- | --- |
| Discord says the redirect URI is invalid | The address you signed in at isn't registered with Discord | Add its `…/auth/callback` URL under **OAuth2 > Redirects** in the Developer Portal and press **Save Changes**. For the web address, **Settings > Remote access** shows the exact URL. See [Remote access](#remote-access) |
| `invalid or expired state` | Sign-in took longer than 10 minutes, finished in another browser, or the browser blocked cookies | Start again from **Continue with Discord** in the same browser |
| `token exchange failed` | Discord didn't accept the client secret, usually because it was reset after setup | Reset the bot's configuration under **Settings > Bots** and set it up again. See [Running multiple bots](#running-multiple-bots) |

If the desktop app's usual port is busy when Olisar starts, the local address changes and has to be registered again. See [First-run setup wizard](#first-run-setup-wizard).

#### The console says Access denied

The account has no **Manage Server** on any approved server Olisar is in. Ask for the permission, or sign in with an account that has it. A server still waiting for the operator's approval doesn't count. A member without **Manage Server** sees the [member portal](#member-portal) instead if a server they share with Olisar has it open.

#### You were signed out without warning

Olisar checks your **Manage Server** permission on every request. Losing it on every server Olisar is in signs you out at once. Admins other than the operator are also signed out when the bot has been offline for more than 5 minutes, and every session ends after 14 days. Sign in again.

#### A server is missing from the server switcher

Olisar reads your servers when you sign in. If you just got **Manage Server** or just added Olisar, press **Log out** and sign in again. A server waiting for the operator's approval doesn't appear until it's approved. See [Servers](#servers).

#### Remote access

#### Other admins can't open the web link

| What you notice | Cause | Fix |
| --- | --- | --- |
| The drawer at the bottom of the sidebar reads **Web access off** | Remote access is off | The operator turns it on under **Settings > Remote access** |
| It reads **Reconnecting…** | The link is set up but the tunnel isn't running, often because the operator's machine is asleep or offline | Wake the machine. If it stays, the operator switches remote access off and on again under **Settings > Remote access** |
| The old link stopped working | The address was renamed | Share the new link, and register its `…/auth/callback` with Discord so sign-in works there |
| The admin gets **Access denied** | Their server is still waiting for approval, or they lack **Manage Server** there | See "The console says Access denied" above |

See [Remote access](#remote-access).

#### Remote access won't turn on

"couldn't join your tailnet" means Tailscale refused the auth key: it's mistyped, expired, revoked or already used. Create a reusable key in Tailscale and try again. An error from Tailscale with a link in it usually means Funnel isn't enabled for your tailnet; follow the link, enable it, and turn remote access on again. For a bot on a VM, the panel says "Tailscale rejected the auth key" and asks for a new one. See [Remote access](#remote-access).

#### Installing

#### Windows says it protected your PC

The Windows installer isn't code-signed, so SmartScreen warns about an unknown publisher. Choose **More info**, then **Run anyway**. See [Install the desktop app](#install-the-desktop-app).

