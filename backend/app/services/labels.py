"""Address labels: built-in lists from `data/labels/<chain>.json` plus user labels in the DB."""

import json
import logging
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from app.db import Database, LabelRow
from app.models import Chain

log = logging.getLogger(__name__)

LABELS_DIR = Path(__file__).resolve().parents[2] / "data" / "labels"


class LabelCategory(str, Enum):
    EXCHANGE = "exchange"
    BRIDGE = "bridge"
    MIXER = "mixer"
    DEFI = "defi"
    TOKEN_CONTRACT = "token_contract"
    SANCTIONED = "sanctioned"
    SCAM = "scam"
    SERVICE = "service"
    PERSONAL = "personal"
    OTHER = "other"


# Money that reaches one of these is pooled with other users' funds, so a
# trace cannot follow it further on-chain.
TERMINAL_CATEGORIES = {
    LabelCategory.EXCHANGE,
    LabelCategory.BRIDGE,
    LabelCategory.MIXER,
    LabelCategory.DEFI,
    LabelCategory.SERVICE,
    LabelCategory.TOKEN_CONTRACT,
}

RISKY_CATEGORIES = {LabelCategory.MIXER, LabelCategory.SANCTIONED, LabelCategory.SCAM}


class Label(BaseModel):
    chain: Chain
    address: str
    name: str
    category: LabelCategory
    note: str | None = None
    source: str  # "builtin" or "user"


def load_builtin_labels(directory: Path = LABELS_DIR) -> dict[tuple[Chain, str], Label]:
    labels: dict[tuple[Chain, str], Label] = {}
    for path in sorted(directory.glob("*.json")):
        # "<chain>.json" or "<chain>.<source>.json", e.g. "tron.ofac.json"
        try:
            chain = Chain(path.name.split(".", 1)[0])
        except ValueError:
            log.warning("ignoring label file for unknown chain: %s", path.name)
            continue
        for address, entry in json.loads(path.read_text(encoding="utf-8")).items():
            # Hand-curated "<chain>.json" sorts before "<chain>.<source>.json" and wins.
            if address.startswith("_") or (chain, address) in labels:
                continue
            labels[(chain, address)] = Label(
                chain=chain,
                address=address,
                name=entry["name"],
                category=LabelCategory(entry["category"]),
                note=entry.get("note"),
                source="builtin",
            )
    return labels


class LabelService:
    def __init__(self, db: Database, builtin: dict[tuple[Chain, str], Label] | None = None):
        self._db = db
        self._builtin = load_builtin_labels() if builtin is None else builtin
        self._user: dict[tuple[Chain, str], Label] = {}

    async def load(self) -> None:
        self._user = {
            (Chain(r.chain), r.address): Label(
                chain=Chain(r.chain),
                address=r.address,
                name=r.name,
                category=LabelCategory(r.category),
                note=r.note,
                source="user",
            )
            for r in await self._db.list_labels()
        }

    def get(self, chain: Chain, address: str) -> Label | None:
        """User labels win over built-in ones."""
        return self._user.get((chain, address)) or self._builtin.get((chain, address))

    def all(self, chain: Chain | None = None) -> list[Label]:
        merged = {**self._builtin, **self._user}
        return [label for (c, _), label in merged.items() if chain is None or c == chain]

    async def set(
        self, chain: Chain, address: str, name: str, category: LabelCategory, note: str | None = None
    ) -> Label:
        await self._db.upsert_label(
            LabelRow(chain=chain.value, address=address, name=name, category=category.value, note=note)
        )
        label = Label(chain=chain, address=address, name=name, category=category, note=note, source="user")
        self._user[(chain, address)] = label
        return label

    async def remove(self, chain: Chain, address: str) -> bool:
        self._user.pop((chain, address), None)
        return await self._db.delete_label(chain, address)
