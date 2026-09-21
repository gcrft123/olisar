"""The Discord half of the tool PIN: the prompt, the form, and the wait.

When a gated tool call comes up mid-reply, :func:`request_pin` posts the configured prompt
into the channel with an **Enter PIN** button, parks the reply on a future, and resolves it
with one of the outcomes in :mod:`olisar.toolpin`. The PIN itself is typed into a Discord
modal, so it is never a message in the channel, never in the bot's message history, and
never in anyone's scrollback — the closest thing Discord has to a password field.

Three rules the flow depends on:

* **One deadline, ours.** discord.py restarts a View's timeout on every interaction, so a
  wrong entry would extend the wait indefinitely. The wait is an ``asyncio.wait_for`` on
  our own future instead, and the View's timeout is only a backstop for cleaning up.
* **The PIN is the authorization.** Anyone who can see the prompt may answer it; knowing
  the four digits is what grants the call, not a Discord permission. A check on top of it
  would only mean someone who was given the PIN couldn't use it.
* **The prompt leaves no trace.** However it ends, the message is deleted. Olisar's own
  reply is what tells the channel whether the call ran, and a stale question with dead
  buttons under it is worse than nothing.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from bot.replies import typing_paused
from olisar import toolpin
from olisar.audit import record_audit
from olisar.db.engine import session_scope
from olisar.messages import get_command_messages, render_message

log = logging.getLogger("olisar.toolpin.discord")

# Ephemeral feedback on the click itself, seen only by whoever clicked. A correct PIN and a
# cancel say nothing at all: the reply that follows is the answer, and a private
# "Confirmed." on top of it is one more thing to read for no information. What's left is
# the two failures and the two errors, where silence would be a mystery.
_WRONG = "Wrong PIN ({used}/{max})."
_EXHAUSTED = "Wrong PIN entered {max} times."
_GONE = "Error: Already answered."
_FORM_FAILED = "Error: Something went wrong with the form."


class _PinModal(discord.ui.Modal):
    """The form. One field, four digits, submitted privately to Discord."""

    def __init__(self, request: "_PinRequest") -> None:
        super().__init__(title="Confirm with your PIN", timeout=300)
        self._request = request
        self.pin = discord.ui.TextInput(
            label="4-digit PIN",
            placeholder="••••",
            min_length=toolpin.PIN_LENGTH,
            max_length=toolpin.PIN_LENGTH,
            required=True,
        )
        self.add_item(self.pin)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._request.submit(interaction, str(self.pin.value or ""))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("PIN modal failed", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message(_FORM_FAILED, ephemeral=True)


class _PinView(discord.ui.View):
    def __init__(self, request: "_PinRequest", timeout: float) -> None:
        # Only a backstop: the real deadline is the wait_for in `request_pin`. Padded so
        # the view is still alive to be tidied up when that deadline lands.
        super().__init__(timeout=timeout + 15)
        self._request = request

    @discord.ui.button(label="Enter PIN", style=discord.ButtonStyle.primary)
    async def enter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self._request.done:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        await interaction.response.send_modal(_PinModal(self._request))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self._request.done:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        if self._request.resolve(toolpin.REFUSED, by=interaction.user.id):
            # Nothing to say: the prompt is about to disappear and Olisar's reply explains
            # itself. Discord still needs the click acknowledged, and a bare defer is the
            # acknowledgement that shows the clicker nothing.
            await interaction.response.defer()
        else:
            await interaction.response.send_message(_GONE, ephemeral=True)


class _PinRequest:
    """One pending confirmation: how many tries have been used, and the future the parked
    reply is waiting on."""

    def __init__(self, *, tool: str, guild_id: int) -> None:
        self.tool = tool
        self.guild_id = guild_id
        self.attempts = 0
        self.outcome = ""
        self.answered_by = 0
        self._future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    @property
    def done(self) -> bool:
        return self._future.done()

    async def submit(self, interaction: discord.Interaction, entered: str) -> None:
        """A PIN came back from the modal. Verify it on its own session — the reply that
        is waiting for this holds one of its own, and two coroutines must not share it."""
        if self.done:
            await interaction.response.send_message(_GONE, ephemeral=True)
            return
        async with session_scope() as session:
            ok = await toolpin.verify(session, entered)
        if ok:
            # The deadline can land while the form is open, so this only counts as the
            # confirmation if it's what actually resolved the request.
            if self.resolve(toolpin.APPROVED, by=interaction.user.id):
                await interaction.response.defer()
            else:
                await interaction.response.send_message(_GONE, ephemeral=True)
            return
        self.attempts += 1
        if self.attempts >= toolpin.MAX_ATTEMPTS:
            self.resolve(toolpin.WRONG, by=interaction.user.id)
            await interaction.response.send_message(
                _EXHAUSTED.format(max=toolpin.MAX_ATTEMPTS), ephemeral=True
            )
            return
        await interaction.response.send_message(
            _WRONG.format(used=self.attempts, max=toolpin.MAX_ATTEMPTS), ephemeral=True
        )

    def resolve(self, outcome: str, *, by: int = 0) -> bool:
        """Settle the request. False if something else got there first."""
        if self.done:
            return False
        self.outcome = outcome
        self.answered_by = by
        self._future.set_result(outcome)
        return True

    async def wait(self, timeout: float) -> str:
        try:
            # Shielded so the deadline expiring cancels the wait and not the future the
            # button callbacks still hold — a click landing in that same moment settles it
            # honestly instead of raising into the interaction handler.
            return await asyncio.wait_for(asyncio.shield(self._future), timeout)
        except asyncio.TimeoutError:
            self.resolve(toolpin.TIMEOUT)
            return self.outcome or toolpin.TIMEOUT


async def request_pin(
    channel: discord.abc.Messageable | None,
    *,
    tool: str,
    guild_id: int,
    user_id: int,
    timeout: float,
) -> str:
    """Ask the channel to confirm ``tool`` with the PIN and wait up to ``timeout`` seconds.

    Returns an outcome from :mod:`olisar.toolpin`. Anything that goes wrong on the Discord
    side (no channel, no permission to post, an API error) resolves to ``UNAVAILABLE``,
    which the tool path treats as a refusal — a confirmation nobody could see is not one.
    """
    if channel is None:
        return toolpin.UNAVAILABLE
    seconds = int(timeout)
    async with session_scope() as session:
        custom = await get_command_messages(session, guild_id)
    prompt = render_message(custom, "tool_pin_prompt", tool=tool, seconds=seconds)

    request = _PinRequest(tool=tool, guild_id=guild_id)
    view = _PinView(request, timeout)
    # Pings nobody, whatever the operator wrote in the template. The prompt is addressed to
    # whoever is already watching the channel, and a server that has told Olisar never to
    # @everyone (GuildConfig.blocked_mentions) must not be talked around by this path.
    silent = discord.AllowedMentions.none()
    try:
        message = await channel.send(prompt, view=view, allowed_mentions=silent)
    except Exception:  # noqa: BLE001
        log.exception("couldn't post the PIN prompt for %s", tool)
        return toolpin.UNAVAILABLE

    log.info("PIN prompt posted for %s (guild %s, %ss)", tool, guild_id, seconds)
    # Olisar isn't writing anything while this sits there, so it shouldn't look like it is.
    with typing_paused():
        outcome = await request.wait(timeout)
    view.stop()

    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — the answer stands whether or not we can tidy up
        log.exception("couldn't remove the PIN prompt for %s", tool)

    log.info(
        "PIN prompt for %s: %s (by %s, %d wrong attempt(s))",
        tool, outcome, request.answered_by or "nobody", request.attempts,
    )
    try:
        async with session_scope() as session:
            await record_audit(
                session,
                actor=request.answered_by or user_id,
                action="tool_pin",
                target_type="tool",
                target_id=tool,
                after={
                    "outcome": outcome,
                    "wrong_attempts": request.attempts,
                    "guild_id": str(guild_id),
                },
            )
    except Exception:  # noqa: BLE001
        log.exception("couldn't record the PIN outcome for %s", tool)
    return outcome
