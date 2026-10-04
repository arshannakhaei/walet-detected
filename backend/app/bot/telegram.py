"""Telegram bot (aiogram 3, long polling) running inside the ChainTrace server.

Only Telegram user ids listed in TELEGRAM_ALLOWED_USERS may use it; anyone
else is told their id so the owner can add it.
"""

import asyncio
import logging
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from app.bot import texts
from app.models import Chain
from app.providers import ProviderError
from app.services.addresses import AddressError, detect_chains, resolve_address
from app.services.graph import GraphParams
from app.services.graph_image import render_png
from app.services.links import LinkParams, resolve_members
from app.services.monitor import Alert
from app.services.tracer import TraceDirection, TraceParams, TraceStartNotFound
from app.services.wallet import TransferFilter, UnsupportedChainError

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000  # message length limit is 4096


def _chunks(text: str) -> list[str]:
    out, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TELEGRAM_LIMIT:
            out.append(current)
            current = ""
        current += line + "\n"
    return out + [current] if current.strip() else out


class TelegramBot:
    def __init__(self, token: str, allowed_users: set[int], services: SimpleNamespace, public_url: str = ""):
        self.bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        self._allowed = allowed_users
        self._s = services
        self._public_url = public_url.rstrip("/")
        self._task: asyncio.Task | None = None
        self._register()

    # --- helpers ------------------------------------------------------------

    def _ok(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id in self._allowed

    async def _deny(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else "?"
        await message.answer(
            f"⛔️ دسترسی ندارید.\nشناسه شما: <code>{uid}</code>\n"
            "برای دسترسی، این شناسه را در TELEGRAM_ALLOWED_USERS فایل .env اضافه کنید."
        )

    def _target(self, address: str, chain_name: str | None) -> tuple[Chain, str]:
        chain = Chain(chain_name.lower()) if chain_name else None
        return resolve_address(address, chain, self._s.providers.supported_chains)

    async def _reply(self, message: Message, text: str) -> None:
        for part in _chunks(text):
            await message.answer(part, disable_web_page_preview=True)

    async def _guard(self, message: Message, work) -> None:
        if not self._ok(message):
            await self._deny(message)
            return
        try:
            await work()
        except (AddressError, ValueError) as exc:
            await message.answer(f"❌ {exc}")
        except UnsupportedChainError as exc:
            await message.answer(f"❌ این شبکه فعال نیست: {exc}")
        except ProviderError as exc:
            await message.answer(f"⚠️ خطا در دریافت داده از بلاکچین:\n{exc}")
        except TraceStartNotFound as exc:
            await message.answer(f"❌ {exc}")
        except Exception:
            log.exception("telegram handler failed")
            await message.answer("⚠️ خطای داخلی. جزئیات در لاگ سرور.")

    # --- handlers -----------------------------------------------------------

    async def wallet(self, message: Message, args: list[str]) -> None:
        if not args:
            raise ValueError("آدرس را وارد کنید: /wallet <address> [chain]")
        chain, address = self._target(args[0], args[1] if len(args) > 1 else None)
        await message.answer("⏳ در حال دریافت تراکنش‌ها...")
        overview = await self._s.wallets.overview(chain, address)
        risk = await self._s.risk.analyze(chain, address)
        cps = await self._s.wallets.counterparties(chain, address, TransferFilter())
        url = f"{self._public_url}/wallet/{chain.value}/{address}" if self._public_url else None
        await self._reply(message, texts.overview_text(overview, risk, cps, self._s.labels, url))
        await self.graph(message, [address, chain.value], announce=False)

    async def graph(self, message: Message, args: list[str], announce: bool = True) -> None:
        if not args:
            raise ValueError("آدرس را وارد کنید: /graph <address> [chain]")
        chain, address = self._target(args[0], args[1] if len(args) > 1 else None)
        if announce:
            await message.answer("⏳ در حال ساخت گراف...")
        graph = await self._s.graphs.build(
            chain, address, GraphParams(depth_in=1, depth_out=2, max_nodes=40, max_children=8)
        )
        if len(graph.nodes) <= 1:
            await message.answer("گرافی برای نمایش وجود ندارد.")
            return
        png = await asyncio.to_thread(render_png, graph)
        await message.answer_photo(
            BufferedInputFile(png, filename="graph.png"),
            caption=f"گراف جریان پول: ۱ لایه ورودی، ۲ لایه خروجی ({len(graph.nodes)} ولت)",
        )

    async def risk(self, message: Message, args: list[str]) -> None:
        if not args:
            raise ValueError("آدرس را وارد کنید: /risk <address> [chain]")
        chain, address = self._target(args[0], args[1] if len(args) > 1 else None)
        report = await self._s.risk.analyze(chain, address, deep=True)
        await self._reply(message, texts.risk_text(report))

    async def trace(self, message: Message, args: list[str]) -> None:
        if len(args) < 2:
            raise ValueError("استفاده: /trace <address> <tx_hash> [amount] [back]")
        chain, address = self._target(args[0], None)
        amount = None
        backward = "back" in [a.lower() for a in args[2:]]
        for extra in args[2:]:
            try:
                amount = Decimal(extra)
            except InvalidOperation:
                continue
        await message.answer("⏳ در حال ردیابی مسیر پول...")
        start = await self._s.tracer.find_start(chain, address, args[1])
        params = TraceParams(direction=TraceDirection.BACKWARD if backward else TraceDirection.FORWARD)
        result = await self._s.tracer.trace(chain, start, params, amount)
        await self._reply(message, texts.trace_text(result))

    async def watch(self, message: Message, args: list[str]) -> None:
        if not args:
            raise ValueError("استفاده: /watch <address> [min_amount] [token]")
        chain, address = self._target(args[0], None)
        min_amount = Decimal(args[1]) if len(args) > 1 else Decimal(0)
        token = args[2] if len(args) > 2 else None
        await self._s.monitor.add(
            chain, address, min_amount=min_amount, token=token, telegram_chat_id=str(message.chat.id)
        )
        await message.answer(f"👁 {texts.code(address)} به واچ‌لیست اضافه شد. تراکنش‌های جدید اطلاع داده می‌شوند.")

    async def unwatch(self, message: Message, args: list[str]) -> None:
        if not args:
            raise ValueError("استفاده: /unwatch <address>")
        removed = 0
        for w in await self._s.monitor.list_watches():
            if w.address.lower() == args[0].strip().lower() and w.telegram_chat_id == str(message.chat.id):
                removed += await self._s.monitor.remove(w.id)
        await message.answer("✅ حذف شد." if removed else "این آدرس در واچ‌لیست شما نیست.")

    async def watchlist(self, message: Message, _args: list[str]) -> None:
        mine = [w for w in await self._s.monitor.list_watches() if w.telegram_chat_id == str(message.chat.id)]
        if not mine:
            await message.answer("واچ‌لیست خالی است. /watch <address>")
            return
        lines = ["👁 <b>واچ‌لیست:</b>"]
        for w in mine:
            cond = f" ≥ {texts.num(w.min_amount)}" if w.min_amount else ""
            lines.append(f"• {w.chain.value}: {texts.code(w.address)}{cond} {w.token or ''}")
        await self._reply(message, "\n".join(lines))

    async def links(self, message: Message, args: list[str]) -> None:
        addresses = [a for a in args if detect_chains(a)]
        if len(addresses) < 2:
            raise ValueError("حداقل دو آدرس بفرستید: /links <address1> <address2> ...")
        chain, members = resolve_members(addresses, None, self._s.providers.supported_chains)
        await message.answer(f"⏳ در حال بررسی ارتباط بین {len(members)} کیف... (ممکن است چند دقیقه طول بکشد)")
        report = await self._s.links.analyzer.analyze(chain, members, LinkParams())
        url = f"{self._public_url}/links" if self._public_url else None
        await self._reply(message, texts.links_text(report, url))

    def _register(self) -> None:
        commands = {
            "wallet": self.wallet,
            "graph": self.graph,
            "risk": self.risk,
            "trace": self.trace,
            "watch": self.watch,
            "unwatch": self.unwatch,
            "watchlist": self.watchlist,
            "links": self.links,
        }
        for name, handler in commands.items():
            async def on_command(message: Message, command: CommandObject, handler=handler):
                await self._guard(message, lambda: handler(message, texts.parse_args(command.args)))

            self.dp.message.register(on_command, Command(name))

        async def on_start(message: Message):
            if not self._ok(message):
                await self._deny(message)
                return
            await message.answer(texts.HELP)

        self.dp.message.register(on_start, Command("start", "help"))

        async def on_text(message: Message):
            # A bare address is treated as /wallet.
            words = texts.parse_args(message.text)
            if sum(1 for w in words if detect_chains(w)) >= 2:  # a pasted list of addresses
                await self._guard(message, lambda: self.links(message, words))
            elif words and detect_chains(words[0]):
                await self._guard(message, lambda: self.wallet(message, words))
            elif self._ok(message):
                await message.answer(texts.HELP)
            else:
                await self._deny(message)

        self.dp.message.register(on_text, F.text)

    # --- lifecycle ----------------------------------------------------------

    async def notify(self, alert: Alert) -> None:
        if alert.telegram_chat_id:
            await self.bot.send_message(alert.telegram_chat_id, texts.alert_text(alert))

    def start(self) -> None:
        self._task = asyncio.create_task(self.dp.start_polling(self.bot, handle_signals=False))

    async def stop(self) -> None:
        if self._task:
            await self.dp.stop_polling()
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, RuntimeError):
                pass
        await self.bot.session.close()
