"""Repository allow-list mapping semantic planner profiles to immutable prompt packs."""

from __future__ import annotations

from types import MappingProxyType

from drawing_planner.prompt_pipeline import prompt_pack_producer_contract


PROFILE_PROMPT_PACKS = MappingProxyType(
    {"production": "native-v4", "debug": "native-v4"}
)


def prompt_pack_for_profile(profile: str) -> str:
    try:
        return PROFILE_PROMPT_PACKS[profile]
    except KeyError as exc:
        raise ValueError(f"unknown planner_profile: {profile}") from exc


def producer_contract_for_profile(profile: str) -> dict[str, str]:
    return prompt_pack_producer_contract(prompt_pack_for_profile(profile))
