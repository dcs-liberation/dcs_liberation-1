from __future__ import annotations

import datetime
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml
from packaging.version import Version

from game.profiling import logged_duration
from game.theater import (
    CaucasusTheater,
    ConflictTheater,
    MarianaIslandsTheater,
    NevadaTheater,
    NormandyTheater,
    PersianGulfTheater,
    SyriaTheater,
    TheChannelTheater,
)
from game.theater.iadsnetwork.iadsnetwork import IadsNetwork
from game.version import CAMPAIGN_FORMAT_VERSION
from .campaignairwingconfig import CampaignAirWingConfig
from .mizcampaignloader import MizCampaignLoader
from .. import persistency

PERF_FRIENDLY = 0
PERF_MEDIUM = 1
PERF_HARD = 2
PERF_NASA = 3
DEFAULT_BUDGET = 2000


@dataclass(frozen=True)
class Campaign:
    name: str
    icon_name: str
    authors: str
    description: str

    #: The revision of the campaign format the campaign was built for. We do not attempt
    #: to migrate old campaigns, but this is used to show a warning in the UI when
    #: selecting a campaign that is not up to date.
    version: Tuple[int, int]

    recommended_player_faction: str
    recommended_enemy_faction: str
    recommended_start_date: datetime.date | None
    recommended_start_time: datetime.time | None

    recommended_player_money: int
    recommended_enemy_money: int
    recommended_player_income_multiplier: float
    recommended_enemy_income_multiplier: float

    performance: int
    data: Dict[str, Any]
    path: Path
    advanced_iads: bool

    @classmethod
    def from_file(cls, path: Path) -> Campaign:
        with path.open() as campaign_file:
            data = yaml.safe_load(campaign_file)

        sanitized_theater = data["theater"].replace(" ", "")
        version_field = data.get("version", "0")
        try:
            version = Version(version_field)
        except TypeError:
            logging.warning(
                f"Non-string campaign version in {path}. Parse may be incorrect."
            )
            version = Version(str(version_field))

        start_date_raw = data.get("recommended_start_date")
        # YAML automatically parses dates.
        start_date: datetime.date | None
        start_time: datetime.time | None = None
        if isinstance(start_date_raw, datetime.datetime):
            start_date = start_date_raw.date()
            start_time = start_date_raw.time()
        elif isinstance(start_date_raw, datetime.date):
            start_date = start_date_raw
            start_time = None
        elif start_date_raw is None:
            start_date = None
        else:
            raise RuntimeError(
                f"Invalid value for recommended_start_date in {path}: {start_date_raw}"
            )

        return cls(
            data["name"],
            f"Terrain_{sanitized_theater}",
            data.get("authors", "???"),
            data.get("description", ""),
            (version.major, version.minor),
            data.get("recommended_player_faction", "USA 2005"),
            data.get("recommended_enemy_faction", "Russia 1990"),
            start_date,
            start_time,
            data.get("recommended_player_money", DEFAULT_BUDGET),
            data.get("recommended_enemy_money", DEFAULT_BUDGET),
            data.get("recommended_player_income_multiplier", 1.0),
            data.get("recommended_enemy_income_multiplier", 1.0),
            data.get("performance", 0),
            data,
            path,
            data.get("advanced_iads", False),
        )

    def load_theater(self, advanced_iads: bool) -> ConflictTheater:
        theaters = {
            "Caucasus": CaucasusTheater,
            "Nevada": NevadaTheater,
            "Persian Gulf": PersianGulfTheater,
            "Normandy": NormandyTheater,
            "The Channel": TheChannelTheater,
            "Syria": SyriaTheater,
            "MarianaIslands": MarianaIslandsTheater,
        }
        theater = theaters[self.data["theater"]]
        t = theater()

        try:
            miz = self.data["miz"]
        except KeyError as ex:
            raise RuntimeError(
                "Old format (non-miz) campaigns are no longer supported."
            ) from ex

        with logged_duration("Importing miz data"):
            MizCampaignLoader(self.path.parent / miz, t).populate_theater()

        # Load IADS Config from campaign yaml
        iads_data = self.data.get("iads_config", [])
        t.iads_network = IadsNetwork(advanced_iads, iads_data)
        return t

    def load_air_wing_config(self, theater: ConflictTheater) -> CampaignAirWingConfig:
        try:
            squadron_data = self.data["squadrons"]
        except KeyError:
            logging.warning(f"Campaign {self.name} does not define any squadrons")
            return CampaignAirWingConfig({})
        return CampaignAirWingConfig.from_campaign_data(squadron_data, theater)

    @property
    def is_out_of_date(self) -> bool:
        """Returns True if this campaign is not up to date with the latest format.

        This is more permissive than is_from_future, which is sensitive to minor version
        bumps (the old game definitely doesn't support the minor features added in the
        new version, and the campaign may require them. However, the minor version only
        indicates *optional* new features, so we do not need to mark out of date
        campaigns as incompatible if they are within the same major version.
        """
        return self.version[0] < CAMPAIGN_FORMAT_VERSION[0]

    @property
    def is_from_future(self) -> bool:
        """Returns True if this campaign is newer than the supported format."""
        return self.version > CAMPAIGN_FORMAT_VERSION

    @property
    def is_compatible(self) -> bool:
        """Returns True is this campaign was built for this version of the game."""
        if self.version == (0, 0):
            return False
        if self.is_out_of_date:
            return False
        if self.is_from_future:
            return False
        return True

    @staticmethod
    def iter_campaigns_in_dir(path: Path) -> Iterator[Path]:
        yield from path.glob("*.yaml")
        yield from path.glob("*.json")

    @classmethod
    def iter_campaign_defs(cls) -> Iterator[Path]:
        yield from cls.iter_campaigns_in_dir(
            Path(persistency.base_path()) / "Liberation/Campaigns"
        )
        yield from cls.iter_campaigns_in_dir(Path("resources/campaigns"))

    @classmethod
    def load_each(cls) -> Iterator[Campaign]:
        for path in cls.iter_campaign_defs():
            try:
                logging.debug(f"Loading campaign from {path}...")
                campaign = Campaign.from_file(path)
                yield campaign
            except RuntimeError:
                logging.exception(f"Unable to load campaign from {path}")
